# workspace

Your own client scripts and simulation configs live here. This directory is
tracked; `output/` next to it is not.

Inside the client container this path is `/workspace/workspace`.

## Scene and robot configs

Copy a starting pair out of the upstream examples and edit them here rather
than editing the submodule, so `git status` stays meaningful:

```bash
mkdir -p workspace/sim_config
cp ProjectAirSim/client/python/example_user_scripts/sim_config/scene_basic_drone.jsonc \
   ProjectAirSim/client/python/example_user_scripts/sim_config/robot_quadrotor_fastphysics.jsonc \
   workspace/sim_config/
```

Point the client at them with `sim_config_path`:

```python
world = World(client, "your_scene.jsonc",
              sim_config_path="/workspace/workspace/sim_config")
```
