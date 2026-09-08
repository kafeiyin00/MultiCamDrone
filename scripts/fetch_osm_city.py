#!/usr/bin/env python3
"""
Build a Project AirSim GIS tile set from OpenStreetMap buildings.

Project AirSim can render a real city without repackaging the environment: set
`scene-type: CustomGIS` and point `tiles-dir` at a directory of .glb tiles named
by Bing Maps quadkey. This produces exactly that from OSM data, which is open
(ODbL) and needs no account -- unlike Cesium ion or Epic's City Sample.

    ./scripts/fetch_osm_city.py --preset singapore-cbd
    ./scripts/fetch_osm_city.py --bbox 1.272,103.840,1.292,103.862 --name mycity

What the tile format requires, all read out of the plugin source rather than
guessed:

  * `<quadkey>.glb`, flat in one directory   (GISRenderer.cpp:501)
  * vertex positions in **absolute ECEF metres** -- the renderer converts
    ECEF->NED itself                          (GISRenderer.cpp:123)
  * exactly one mesh with one primitive per file, and TEXCOORD_0 is read
    unconditionally, so UVs are mandatory     (gltf_data_provider.cpp:66-70)

Known limitation, inherent to that format: positions are float32 (Vertex =
Vector3f), and absolute ECEF near Singapore is ~6.378e6 m, so vertices quantise
to about 0.38 m. Cesium avoids this with a per-tile RTC_CENTER translation, but
this parser ignores node transforms, and glTF 2.0 requires POSITION to be FLOAT.
Fixing it properly means adding a per-tile origin on the plugin side.
"""

import argparse
import json
import math
import os
import struct
import sys
import zlib
import urllib.parse
import urllib.request

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = 2 * WGS84_F - WGS84_F * WGS84_F

OVERPASS = "https://overpass-api.de/api/interpreter"

PRESETS = {
    # Raffles Place, Marina Bay, Shenton Way, Tanjong Pagar -- the tower cluster.
    "singapore-cbd": (1.2720, 103.8400, 1.2920, 103.8620),
    "manhattan-midtown": (40.7440, -73.9930, 40.7620, -73.9700),
}

DEFAULT_LEVEL_HEIGHT = 3.2   # metres, when only building:levels is tagged
DEFAULT_HEIGHT = 12.0        # metres, when nothing is tagged
UV_METRES_PER_TILE = 8.0     # texture repeat distance, only affects UV scale


# --------------------------------------------------------------------------
# Geodesy and tiling
# --------------------------------------------------------------------------

def geodetic_to_ecef(lat_deg, lon_deg, h):
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    return ((n + h) * cos_lat * math.cos(lon),
            (n + h) * cos_lat * math.sin(lon),
            (n * (1.0 - WGS84_E2) + h) * sin_lat)


def lonlat_to_tile(lon_deg, lat_deg, lod):
    """Web-Mercator tile indices, the scheme Bing quadkeys encode."""
    n = 1 << lod
    x = int((lon_deg + 180.0) / 360.0 * n)
    lat = math.radians(max(-85.05112878, min(85.05112878, lat_deg)))
    y = int((1.0 - math.log(math.tan(lat) + 1.0 / math.cos(lat)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile_to_quadkey(x, y, lod):
    out = []
    for i in range(lod, 0, -1):
        bit = 1 << (i - 1)
        digit = 0
        if x & bit:
            digit += 1
        if y & bit:
            digit += 2
        out.append(str(digit))
    return "".join(out)


# --------------------------------------------------------------------------
# OSM
# --------------------------------------------------------------------------

def fetch_buildings(bbox, timeout=180):
    south, west, north, east = bbox
    query = (
        f"[out:json][timeout:{timeout}];"
        f'way["building"]({south},{west},{north},{east});'
        f"out geom tags;"
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS, data=data,
                                 headers={"User-Agent": "MultiCamDrone/1.0"})
    with urllib.request.urlopen(req, timeout=timeout + 60) as fh:
        return json.load(fh)


def parse_height(tags):
    """Metres. OSM height tags are messy: '123', '123 m', '123.5'."""
    raw = tags.get("height") or tags.get("building:height")
    if raw:
        try:
            return max(2.0, float(str(raw).split()[0].replace(",", ".")))
        except ValueError:
            pass
    levels = tags.get("building:levels")
    if levels:
        try:
            return max(2.0, float(str(levels).split(";")[0]) * DEFAULT_LEVEL_HEIGHT)
        except ValueError:
            pass
    return DEFAULT_HEIGHT


# --------------------------------------------------------------------------
# Meshing
# --------------------------------------------------------------------------

def triangulate(points_xy):
    """Ear clipping for a simple polygon. Returns index triples.

    OSM footprints are simple and usually small, so a plain O(n^2) ear clip is
    both adequate and dependency-free. Concave shapes are handled; holes and
    multipolygon relations are not (see --skip-relations note in the docstring).
    """
    n = len(points_xy)
    if n < 3:
        return []

    def area2(a, b, c):
        return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))

    # Work counter-clockwise so a positive cross product means convex.
    idx = list(range(n))
    signed = sum(points_xy[i][0] * points_xy[(i + 1) % n][1]
                 - points_xy[(i + 1) % n][0] * points_xy[i][1] for i in range(n))
    if signed < 0:
        idx.reverse()

    def inside(p, a, b, c):
        d1 = area2(a, b, p)
        d2 = area2(b, c, p)
        d3 = area2(c, a, p)
        return (d1 >= 0 and d2 >= 0 and d3 >= 0)

    tris = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n:
        guard += 1
        clipped = False
        for i in range(len(idx)):
            ia, ib, ic = idx[i - 1], idx[i], idx[(i + 1) % len(idx)]
            a, b, c = points_xy[ia], points_xy[ib], points_xy[ic]
            if area2(a, b, c) <= 0:
                continue                      # reflex, not an ear
            if any(inside(points_xy[j], a, b, c)
                   for j in idx if j not in (ia, ib, ic)):
                continue                      # another vertex is inside
            tris.append((ia, ib, ic))
            idx.pop(i)
            clipped = True
            break
        if not clipped:
            break                             # degenerate; keep what we have
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


# Buildings carry no material in OSM, and an untextured city renders as a white
# blob. The parser does load a texture (gltf_data_provider.cpp:172, decoded by
# stb through tinygltf), and each tile is limited to one primitive, so per-
# building colour has to come from UVs into a shared palette rather than from
# separate materials.
#
# Row 0 is wall colours, row 1 is roof colours; a building picks a column and
# its UVs land in the middle of that cell.
PALETTE_WALLS = [
    (196, 198, 203), (168, 172, 180), (149, 156, 166), (182, 176, 168),
    (160, 150, 140), (134, 142, 152), (176, 182, 188), (144, 148, 156),
]
PALETTE_ROOFS = [
    (108, 112, 120), (92, 96, 104), (120, 116, 110), (84, 88, 96),
    (130, 126, 118), (100, 106, 114), (114, 118, 126), (88, 92, 100),
]
GROUND_COLOUR = (74, 76, 80)
PALETTE_N = len(PALETTE_WALLS) + 1          # +1 column for the ground
PALETTE_H = 2


def palette_uv(col, row):
    """Centre of a palette cell, so bilinear filtering cannot bleed neighbours."""
    return ((col + 0.5) / PALETTE_N, (row + 0.5) / PALETTE_H)


def palette_png():
    """The palette as a PNG, small enough to embed in every tile."""
    rows = []
    for r in range(PALETTE_H):
        row = bytearray()
        src = PALETTE_WALLS if r == 0 else PALETTE_ROOFS
        for c in range(PALETTE_N):
            rgb = GROUND_COLOUR if c == len(src) else src[c]
            row += bytes(rgb) + b"\xff"
        rows.append(bytes(row))
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    hdr = struct.pack(">IIBBBBB", PALETTE_N, PALETTE_H, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", hdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class TileMesh:
    """Accumulates one tile's geometry: absolute-ECEF positions, UVs, indices."""

    def __init__(self):
        self.pos = []
        self.uv = []
        self.idx = []

    def add(self, positions, uvs, triangles):
        base = len(self.pos)
        self.pos.extend(positions)
        self.uv.extend(uvs)
        for a, b, c in triangles:
            self.idx.extend((base + a, base + b, base + c))


def tile_bounds(x, y, lod):
    """Lat/lon corners of a Web-Mercator tile."""
    n = 1 << lod
    def lon(i): return i / n * 360.0 - 180.0
    def lat(j):
        t = math.pi * (1 - 2 * j / n)
        return math.degrees(math.atan(math.sinh(t)))
    return lat(y + 1), lon(x), lat(y), lon(x + 1)   # south, west, north, east


def build_ground(x, y, lod, mesh):
    """A flat quad covering one tile at sea level.

    Without it the city is buildings floating over a void: the drone falls
    straight through, since tiles are the only collision geometry a GIS scene
    has. Two triangles per tile is enough -- the surface is flat.
    """
    south, west, north, east = tile_bounds(x, y, lod)
    corners = [(south, west), (south, east), (north, east), (north, west)]
    uv = palette_uv(PALETTE_N - 1, 0)      # the ground column
    positions = [geodetic_to_ecef(lat, lon, 0.0) for lat, lon in corners]
    mesh.add(positions, [uv] * 4, [(0, 1, 2), (0, 2, 3)])


def build_building(ring_latlon, height, mesh, colour=0):
    """Walls plus a flat roof for one footprint, appended to `mesh`."""
    # Drop the repeated closing node OSM includes.
    ring = ring_latlon[:-1] if (len(ring_latlon) > 3
                                and ring_latlon[0] == ring_latlon[-1]) else ring_latlon
    if len(ring) < 3:
        return 0

    lat0 = sum(p[0] for p in ring) / len(ring)
    lon0 = sum(p[1] for p in ring) / len(ring)
    # Local metres about the footprint centroid, for triangulation and UVs.
    m_per_deg_lat = 111132.92
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))
    local = [(((p[1] - lon0) * m_per_deg_lon), ((p[0] - lat0) * m_per_deg_lat))
             for p in ring]

    wall_uv = palette_uv(colour, 0)
    roof_uv = palette_uv(colour, 1)

    # --- walls: one quad per edge -------------------------------------
    positions, uvs, tris = [], [], []
    run = 0.0
    for i in range(len(ring)):
        j = (i + 1) % len(ring)
        seg = math.dist(local[i], local[j])
        if seg < 1e-6:
            continue
        b = len(positions)
        for (lat, lon), u in (((ring[i][0], ring[i][1]), run),
                              ((ring[j][0], ring[j][1]), run + seg)):
            positions.append(geodetic_to_ecef(lat, lon, 0.0))
            uvs.append(wall_uv)
        for (lat, lon), u in (((ring[i][0], ring[i][1]), run),
                              ((ring[j][0], ring[j][1]), run + seg)):
            positions.append(geodetic_to_ecef(lat, lon, height))
            uvs.append(wall_uv)
        # b+0 bottom-i, b+1 bottom-j, b+2 top-i, b+3 top-j
        tris.append((b + 0, b + 1, b + 3))
        tris.append((b + 0, b + 3, b + 2))
        run += seg

    # --- roof ---------------------------------------------------------
    roof_tris = triangulate(local)
    if roof_tris:
        b = len(positions)
        for (lat, lon), (lx, ly) in zip(ring, local):
            positions.append(geodetic_to_ecef(lat, lon, height))
            uvs.append(roof_uv)
        for a, bb, c in roof_tris:
            tris.append((b + a, b + bb, b + c))

    if not tris:
        return 0
    mesh.add(positions, uvs, tris)
    return len(tris)


# --------------------------------------------------------------------------
# glB
# --------------------------------------------------------------------------

def write_glb(path, mesh):
    """Minimal glTF 2.0 binary: one mesh, one primitive, POSITION + TEXCOORD_0.

    The plugin's parser reads meshes[0].primitives[0] and dereferences
    TEXCOORD_0 without checking, so both are required and there must be exactly
    one primitive.
    """
    pos = struct.pack(f"<{len(mesh.pos) * 3}f",
                      *[c for v in mesh.pos for c in v])
    uv = struct.pack(f"<{len(mesh.uv) * 2}f",
                     *[c for v in mesh.uv for c in v])
    use_short = len(mesh.pos) <= 65535
    idx = (struct.pack(f"<{len(mesh.idx)}H", *mesh.idx) if use_short
           else struct.pack(f"<{len(mesh.idx)}I", *mesh.idx))

    def pad4(b, fill=b"\x00"):
        return b + fill * ((4 - len(b) % 4) % 4)

    pos, uv, idx = pad4(pos), pad4(uv), pad4(idx)
    blob = pos + uv + idx

    mins = [min(v[i] for v in mesh.pos) for i in range(3)]
    maxs = [max(v[i] for v in mesh.pos) for i in range(3)]

    png = palette_png()
    png_off = len(blob)
    blob = blob + png + b"\x00" * ((4 - len(png) % 4) % 4)

    gltf = {
        "asset": {"version": "2.0", "generator": "MultiCamDrone fetch_osm_city"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        # No transform on the node: the parser ignores node matrices, so the
        # positions must already be absolute ECEF.
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
            "indices": 2,
            "material": 0,
            "mode": 4,
        }]}],
        "materials": [{"pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0},
            "metallicFactor": 0.0, "roughnessFactor": 0.9}}],
        "textures": [{"source": 0, "sampler": 0}],
        # NEAREST: the palette is one texel per colour, so any filtering would
        # blend neighbouring entries into each other.
        "samplers": [{"magFilter": 9728, "minFilter": 9728,
                      "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": 3, "mimeType": "image/png"}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(uv), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos) + len(uv), "byteLength": len(idx),
             "target": 34963},
            {"buffer": 0, "byteOffset": png_off, "byteLength": len(png)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(mesh.pos),
             "type": "VEC3", "min": mins, "max": maxs},
            {"bufferView": 1, "componentType": 5126, "count": len(mesh.uv),
             "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123 if use_short else 5125,
             "count": len(mesh.idx), "type": "SCALAR"},
        ],
    }

    js = pad4(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    total = 12 + 8 + len(js) + 8 + len(blob)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        fh.write(struct.pack("<II", len(blob), 0x004E4942) + blob)


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--bbox", help="south,west,north,east in degrees")
    ap.add_argument("--name", default=None, help="output directory name")
    ap.add_argument("--lod-min", type=int, default=15)
    ap.add_argument("--lod-max", type=int, default=17)
    ap.add_argument("--no-ground", action="store_true",
                    help="omit the ground quads. Buildings then float over a "
                         "void and anything with physics falls through")
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args()

    if args.bbox:
        bbox = tuple(float(v) for v in args.bbox.split(","))
        if len(bbox) != 4:
            print("error: --bbox needs south,west,north,east", file=sys.stderr)
            return 1
        name = args.name or "city"
    elif args.preset:
        bbox = PRESETS[args.preset]
        name = args.name or args.preset
    else:
        print("error: give --preset or --bbox", file=sys.stderr)
        return 1

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_root = args.out_root or os.path.join(repo, "envs", "gis_tiles")
    out_dir = os.path.join(out_root, name)
    os.makedirs(out_dir, exist_ok=True)

    south, west, north, east = bbox
    print(f"area   : {south},{west} .. {north},{east}")
    print(f"output : {out_dir}")
    print("fetching OSM buildings (ODbL, www.openstreetmap.org) ...")
    data = fetch_buildings(bbox)
    ways = [e for e in data.get("elements", []) if e.get("type") == "way"]
    print(f"  {len(ways)} building ways")
    if not ways:
        print("error: no buildings returned", file=sys.stderr)
        return 1

    total_tris = 0
    skipped = 0
    for lod in range(args.lod_min, args.lod_max + 1):
        tiles = {}

        # Ground first, for every tile the bbox touches -- not just the ones
        # with buildings. Tiles are the only collision geometry a GIS scene
        # has, so gaps are holes the drone falls through.
        if not args.no_ground:
            x0, y0 = lonlat_to_tile(west, north, lod)
            x1, y1 = lonlat_to_tile(east, south, lod)
            for tx in range(min(x0, x1), max(x0, x1) + 1):
                for ty in range(min(y0, y1), max(y0, y1) + 1):
                    build_ground(tx, ty, lod, tiles.setdefault((tx, ty), TileMesh()))
                    total_tris += 2

        for way in ways:
            geom = way.get("geometry")
            if not geom or len(geom) < 4:
                skipped += 1
                continue
            ring = [(p["lat"], p["lon"]) for p in geom]
            clat = sum(p[0] for p in ring) / len(ring)
            clon = sum(p[1] for p in ring) / len(ring)
            key = lonlat_to_tile(clon, clat, lod)
            # Stable per-building colour: keyed on the OSM id so the same
            # building keeps its colour across LODs and across re-runs.
            colour = way.get("id", 0) % len(PALETTE_WALLS)
            tris = build_building(ring, parse_height(way.get("tags", {})),
                                  tiles.setdefault(key, TileMesh()), colour)
            if tris == 0:
                skipped += 1
            total_tris += tris

        written = 0
        for (x, y), mesh in tiles.items():
            if not mesh.idx:
                continue
            write_glb(os.path.join(out_dir, f"{tile_to_quadkey(x, y, lod)}.glb"), mesh)
            written += 1
        print(f"  lod {lod}: {written} tiles")

    size = sum(os.path.getsize(os.path.join(out_dir, f))
               for f in os.listdir(out_dir)) / 1e6
    print(f"\n{total_tris} triangles, {size:.1f} MB"
          + (f", {skipped} footprints skipped as degenerate" if skipped else ""))
    print(f"""
Point a scene config at it:

  "scene-type": "CustomGIS",
  "tiles-dir": "{out_dir}",
  "tiles-lod-min": {args.lod_min},
  "tiles-lod-max": {args.lod_max},
  "home-geo-point": {{ "latitude": {(south+north)/2:.6f}, "longitude": {(west+east)/2:.6f}, "altitude": 0 }}

Inside the sim container that path is /environments/gis_tiles/{name}.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
