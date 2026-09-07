"""
Fly the MultiCamDrone rig around a closed circuit, forever, through ROS2.

WHY THIS IS A ROS2 CLIENT AND NOT A NATIVE ONE
----------------------------------------------
The simulator's topic channel (port 8989) is an NNG **Pair0** socket --
core_sim/src/topic_manager.cpp calls nng_pair0_open -- and Pair0 is strictly
point-to-point: exactly one peer. So only ONE native Project AirSim client can
receive topics at a time. A second one connects at the TCP level and then
silently receives nothing.

That means a native Python flight script cannot coexist with the ROS2 bridge:
whichever connects first owns the pair, and the other sees zero topics. The
symptom is Foxglove listing topics with no data.

The services channel (8990) is Req0/Rep0 and does serve multiple clients, but
`client.connect()` opens both sockets, so a native client always takes the pair.

So the bridge is the single native client, and flight commands go through the
ROS2 services and action it exposes.

    ./scripts/run_flight.sh                       # 40m square, 12m up, 5 m/s
    ./scripts/run_flight.sh --shape figure8 --size 30
    ./scripts/run_flight.sh --laps 3

Ctrl-C lands and disarms rather than leaving the drone hovering.
"""

import argparse
import math
import signal
import sys
import threading

import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.action import ActionClient
from rclpy.node import Node

from projectairsim_ros2_cpp.action import MoveOnPath
from projectairsim_ros2_cpp.srv import Arm, Disarm, Land, Takeoff


def square(size, altitude):
    """Corners of a square circuit centred on the origin, in NED metres."""
    h = size / 2.0
    return [(h, -h), (h, h), (-h, h), (-h, -h)]


def circle(size, altitude, points=16):
    r = size / 2.0
    return [(r * math.cos(2 * math.pi * i / points),
             r * math.sin(2 * math.pi * i / points)) for i in range(points)]


def figure_eight(size, altitude, points=24):
    """A lemniscate. More rotation than a circle, which suits VIO datasets."""
    a = size / 2.0
    pts = []
    for i in range(points):
        t = 2 * math.pi * i / points
        d = 1 + math.sin(t) ** 2
        pts.append((a * math.cos(t) / d, a * math.sin(t) * math.cos(t) / d))
    return pts


SHAPES = {"square": square, "circle": circle, "figure8": figure_eight}


class Pilot(Node):
    def __init__(self, args):
        super().__init__("multicam_pilot")
        self.args = args
        root = f"{args.service_root}/{args.vehicle}"

        self.cli_arm = self.create_client(Arm, f"{root}/arm")
        self.cli_disarm = self.create_client(Disarm, f"{root}/disarm")
        self.cli_takeoff = self.create_client(Takeoff, f"{root}/takeoff")
        self.cli_land = self.create_client(Land, f"{root}/land")
        self.act_path = ActionClient(self, MoveOnPath, f"{root}/move_on_path")

    def wait_for_bridge(self, timeout=60.0):
        for name, cli in (("takeoff", self.cli_takeoff), ("land", self.cli_land),
                          ("disarm", self.cli_disarm)):
            if not cli.wait_for_service(timeout_sec=timeout):
                self.get_logger().error(
                    f"service {name} never appeared. Is the ROS2 bridge running "
                    f"with a scene loaded?")
                return False
        if not self.act_path.wait_for_server(timeout_sec=timeout):
            self.get_logger().error("move_on_path action server never appeared")
            return False
        return True

    def call(self, client, request, what, timeout=60.0):
        """Service call with a deadline. Returns the response, or None."""
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
        if not future.done():
            self.get_logger().error(f"{what} did not return within {timeout:g}s")
            return None
        if future.result() is None:
            self.get_logger().error(f"{what} failed: no response")
            return None
        if not future.result().success:
            self.get_logger().error(f"{what} returned success=false")
        return future.result()

    def make_path(self, xy_points):
        """Turn (north, east) pairs into a PoseStamped path at circuit altitude."""
        stamp = self.get_clock().now().to_msg()
        path = []
        for north, east in xy_points:
            pose = PoseStamped()
            pose.header.stamp = stamp
            pose.header.frame_id = "map"
            pose.pose.position.x = float(north)
            pose.pose.position.y = float(east)
            pose.pose.position.z = float(-self.args.altitude)   # NED: down is +z
            pose.pose.orientation.w = 1.0
            path.append(pose)
        return path

    def fly_path(self, xy_points):
        """Send one lap and block until the action completes. True if it flew."""
        goal = MoveOnPath.Goal()
        goal.path = self.make_path(xy_points)
        goal.velocity = float(self.args.speed)
        goal.timeout_sec = float(self.args.lap_timeout)
        goal.drive_train_type = 1        # ForwardOnly: nose follows the path
        goal.yaw_is_rate = False
        goal.yaw = 0.0
        goal.lookahead = -1.0            # let the carrot algorithm choose
        goal.adaptive_lookahead = 1.0
        # Must be true. The bridge does
        #     return !wait_on_last_task || result.Wait() == Status::OK;
        # so with false it reports success the instant the command is dispatched,
        # and the lap loop spins thousands of times per minute without flying.
        goal.wait_on_last_task = True

        send = self.act_path.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, send)
        handle = send.result()
        if handle is None or not handle.accepted:
            self.get_logger().error("move_on_path goal rejected")
            return False

        result_future = handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future)
        res = result_future.result()
        return bool(res and res.result and res.result.success)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shape", choices=sorted(SHAPES), default="square")
    ap.add_argument("--size", type=float, default=40.0, help="circuit width, m")
    ap.add_argument("--altitude", type=float, default=12.0, help="height, m")
    ap.add_argument("--speed", type=float, default=5.0, help="m/s")
    ap.add_argument("--laps", type=int, default=0, help="0 = forever")
    ap.add_argument("--lap-timeout", type=float, default=300.0)
    ap.add_argument("--vehicle", default="Drone1")
    ap.add_argument("--service-root", default="/projectairsim")
    args = ap.parse_args()

    # Close the circuit so consecutive laps join instead of cutting the corner.
    waypoints = SHAPES[args.shape](args.size, args.altitude)
    lap_path = waypoints + [waypoints[0]]

    rclpy.init()
    node = Pilot(args)
    stop = threading.Event()

    # rclpy only turns SIGINT into KeyboardInterrupt. Without this, a SIGTERM
    # (what `pkill` sends) kills the process outright and leaves the drone
    # armed at altitude, which then blocks the next pilot's takeoff.
    def _on_term(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _on_term)

    try:
        if not node.wait_for_bridge():
            return 1

        node.get_logger().info(
            f"flying {args.shape} {args.size:g}m at {args.altitude:g}m, "
            f"{args.speed:g} m/s, {'forever' if args.laps == 0 else str(args.laps) + ' laps'}")

        # Do NOT arm first. The bridge's Takeoff already calls EnableAndArm
        # internally (EnableAPIControl then Arm), and arming an already-armed
        # drone returns an error, which makes Takeoff fail immediately with
        # success=false. See projectairsim_ros2_cpp_node.cpp Takeoff/EnableAndArm.
        node.get_logger().info("taking off (the bridge arms and takes API control) ...")
        res = node.call(node.cli_takeoff, Takeoff.Request(wait_on_last_task=True),
                        "takeoff", timeout=45.0)
        if res is None or not res.success:
            node.get_logger().warn(
                "takeoff did not confirm; continuing anyway -- the drone may "
                "already be airborne, and the circuit command climbs to altitude")

        lap = 0
        while not stop.is_set() and (args.laps == 0 or lap < args.laps):
            lap += 1
            node.get_logger().info(f"--- lap {lap}"
                                   + (f"/{args.laps}" if args.laps else "") + " ---")
            if not node.fly_path(lap_path):
                node.get_logger().warn(f"lap {lap} did not complete; retrying")
        return 0

    except KeyboardInterrupt:
        node.get_logger().info("interrupted -- landing")
        return 0
    finally:
        # Never leave the drone armed and airborne, whatever happened above.
        try:
            node.get_logger().info("landing ...")
            node.call(node.cli_land, Land.Request(wait_on_last_task=True), "land")
            node.call(node.cli_disarm, Disarm.Request(wait_on_last_task=False), "disarm")
        except Exception:
            pass
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
