"""GLSL programs (milestone H, phase 2, shaders slice).

Wall and plane programs evaluate the software pixel pipeline per
fragment: palette-index sample, light-row lookup (scalelight for
walls, zlight for planes, from the same tables as renderer.py),
COLORMAP remap, PLAYPAL to RGB. Module import never touches GL;
compile/link happens against a live context (raises with the info
log; tests skip without a context).

Wall lighting mirrors _render_seg_loop exactly (same formula, same
inputs, continuous per fragment instead of linearly stepped per
column): scale = 160*sinb/(dperp*sina) with sina/sinb as dot
products (convention-free), index = scale*16 clamped. V is fully
static (v = texbase - z: wall textures are world-pinned, the viewz
inside texturemid only cancels the projection's viewz). U is the
baked texel offset, perspective-interpolated (the software
texturecolumn describes the same projective mapping).

Plane lighting mirrors _map_plane: index = |z-viewz|*10/dy_320 with
dy from the fragment row (horizon guards to darkest, like the
software's huge yslope). Flats sample (x,-y) mod 64 in world units (R_MapPlane
negates viewy).
"""

from __future__ import annotations

__all__ = [
    "AUTO_FRAG",
    "AUTO_VERT",
    "BLOOM_COMBINE_FRAG",
    "BLOOM_EXTRACT_FRAG",
    "FUZZ_FRAG",
    "OVERLAY_FRAG",
    "OVERLAY_VERT",
    "PLANE_FRAG",
    "PLANE_FRAG_LIN",
    "PLANE_VERT",
    "PSPRITE_FRAG",
    "PSPRITE_VERT",
    "SKY_FRAG",
    "SKY_VERT",
    "SPRITE_FRAG",
    "SPRITE_VERT",
    "TEXT_FRAG",
    "TEXT_VERT",
    "WALL_FRAG",
    "WALL_FRAG_DOUBLE",
    "WALL_FRAG_LIN",
    "WALL_DOUBLE_LIN",
    "WALL_VERT",
    "compile_program",
]

WALL_VERT = """\
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in float aU;
layout(location = 2) in float aTexBase;
layout(location = 3) in float aSector;
layout(location = 4) in float aTweak;
layout(location = 5) in vec2 aNormal;
uniform mat4 uViewProj;
out vec2 vUv;
out float vSector;
out float vTweak;
out vec2 vNormal;
out vec2 vWorld;
out float vWorldZ;
void main() {
    gl_Position = uViewProj * vec4(aPos, 1.0);
    vUv = vec2(aU, aTexBase - aPos.z);
    vSector = aSector;
    vTweak = aTweak;
    vNormal = aNormal;
    vWorld = aPos.xy;
    vWorldZ = aPos.z;
}
"""

WALL_FRAG = """\
#version 330 core
in vec2 vUv;
in float vSector;
in float vTweak;
in vec2 vNormal;
in vec2 vWorld;
in float vWorldZ;
uniform sampler2D uWallTex;
uniform sampler2D uScaleLight;
uniform sampler2D uColormap;
uniform sampler2DArray uPalette;
uniform sampler2D uSectorLight;
uniform int uPalIndex;
uniform vec2 uViewPos;
uniform vec2 uViewDir;
uniform int uExtraLight;
uniform int uFullbright;
uniform float uWrap;
uniform float uTexH;
uniform int uDynNum;
uniform vec4 uDynPos[8];
uniform vec4 uDynCol[8];
uniform int uBrightmaps;
uniform sampler2D uBrightLut;
out vec4 oColor;
layout(location = 1) out float oIndex;
void main() {
    vec2 rg = texelFetch(uWallTex,
                         ivec2(int(mod(vUv.x, uWrap)),
                               int(mod(vUv.y, uTexH))), 0).rg;
    if (rg.g < 0.5) discard;
    int idx = int(rg.r * 255.0 + 0.5);
    int lit;
    if (uFullbright != 0) {
        lit = idx;
    } else {
        vec2 f = vWorld - uViewPos;
        float dist = max(length(f), 1e-6);
        vec2 fdir = f / dist;
        // NOTE: front-unit normals point back at the viewer, so den
        // is NEGATIVE on visible faces (f runs into the wall while
        // the normal runs out of it); den >= 0 means a backface or
        // a silhouette edge, which keeps the brightest entry (the
        // software never draws those: the BSP culls them).
        float den = dot(f, vNormal) * dot(fdir, uViewDir);
        int li = 47;
        if (den < 0.0)
            li = clamp(int(160.0 * dot(fdir, vNormal) / den * 16.0),
                       0, 47);
        // NOTE: base lightnum from the sector-light texture (dynamic
        // sectors: flicker/strobe re-upload only that texture).
        // Double clamp matches the old baked formula verbatim
        // (clamp(base + tweak) then + extralight, clamped again).
        float base = texelFetch(uSectorLight,
                                ivec2(int(vSector + 0.5), 0), 0).r
                     * 255.0;
        int row0 = clamp(int(base + 0.5) + int(vTweak + 0.5) - 1,
                         0, 15);
        int row = clamp(row0 + uExtraLight, 0, 15);
        // NOTE: step 8 brightmaps (opt-in): lamp whites/yellows take
        // the brightest ramp (row 15: rows index lightnum, higher =
        // brighter) instead of the distance-dimmed row, via the
        // 256-entry bright LUT. uBrightmaps defaults to 0: vanilla
        // rows, v1-identical pixels.
        int row_use = row;
        if (uBrightmaps != 0
                && texelFetch(uBrightLut, ivec2(idx, 0), 0).r > 0.5)
            row_use = 15;
        int cmap = int(texelFetch(uScaleLight, ivec2(li, row_use), 0).r
                       * 255.0 + 0.5);
        lit = int(texelFetch(uColormap, ivec2(idx, cmap), 0).r
                  * 255.0 + 0.5);
    }
    // NOTE: v2 dynlights (step 6, opt-in): GZDoom-style linear falloff
    // added over the palettized base. uDynNum defaults to 0, so v1 and
    // lights-off v2 evaluate the identical pixels as before.
    vec3 dyn = vec3(0.0);
    vec3 fragPos = vec3(vWorld, vWorldZ);
    for (int i = 0; i < 8; i++) {
        if (i >= uDynNum) break;
        vec3 ld = uDynPos[i].xyz - fragPos;
        float att = clamp(1.0 - length(ld) / uDynPos[i].w, 0.0, 1.0);
        dyn += uDynCol[i].rgb * (att * uDynCol[i].a);
    }
    oColor = vec4(min(texelFetch(uPalette, ivec3(lit, 0, uPalIndex), 0).rgb + dyn, vec3(1.0)), 1.0);
    // NOTE: the index target feeds the fuzz backdrop (R_DrawFuzzColumn
    // remaps DRAWN pixels): store the lit index like the framebuffer
    // holds, not the raw texel (single-darkening would wash spectres
    // out to gray).
    oIndex = float(lit) / 255.0;
}
"""

WALL_FRAG_DOUBLE = WALL_FRAG.replace(
    "        int li = 47;\n"
    "        if (den < 0.0)",
    "        int li = 47;\n"
    "        // NOTE: single-sided backs mirror the front lighting: a\n"
    "        // mirrored camera would see the front with both dots\n"
    "        // flipped, i.e. the same ratio, so the guard takes the\n"
    "        // absolute value (silhouettes keep the brightest entry).\n"
    "        if (abs(den) > 1e-9)",
)
assert WALL_FRAG_DOUBLE != WALL_FRAG  # NOTE: guard text moved on

# NOTE: step 7 linear-filter variants (v2 opt-in): texelFetch ignores
# sampler state, so smoothing needs normalized texture() sampling
# (REPEAT wrap on the v2 sampler tiles long walls/flats exactly like
# the mod() folds above). Derived, never hand-forked: the asserts
# below fail loudly if the base sources drift.
WALL_FRAG_LIN = WALL_FRAG.replace(
    "    vec2 rg = texelFetch(uWallTex,\n"
    "                         ivec2(int(mod(vUv.x, uWrap)),\n"
    "                               int(mod(vUv.y, uTexH))), 0).rg;",
    "    vec2 rg = texture(uWallTex, vec2((vUv.x + 0.5) / uWrap,\n"
    "                                     (vUv.y + 0.5) / uTexH)).rg;",
)
assert WALL_FRAG_LIN != WALL_FRAG  # NOTE: base sample text moved on
WALL_DOUBLE_LIN = WALL_FRAG_LIN.replace(
    "        int li = 47;\n"
    "        if (den < 0.0)",
    "        int li = 47;\n"
    "        // NOTE: single-sided backs mirror the front lighting: a\n"
    "        // mirrored camera would see the front with both dots\n"
    "        // flipped, i.e. the same ratio, so the guard takes the\n"
    "        // absolute value (silhouettes keep the brightest entry).\n"
    "        if (abs(den) > 1e-9)",
)
assert WALL_DOUBLE_LIN != WALL_FRAG_LIN  # NOTE: guard text moved on

PLANE_VERT = """\
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec2 aUv;
layout(location = 2) in float aFlat;
layout(location = 3) in float aSector;
uniform mat4 uViewProj;
out vec2 vUv;
out float vFlat;
out float vSector;
out float vZ;
out vec2 vWorld;
void main() {
    gl_Position = uViewProj * vec4(aPos, 1.0);
    vUv = aUv;
    vFlat = aFlat;
    vSector = aSector;
    vZ = aPos.z;
    vWorld = aPos.xy;
}
"""

PLANE_FRAG = """\
#version 330 core
in vec2 vUv;
in float vFlat;
in float vSector;
in float vZ;
in vec2 vWorld;
uniform sampler2DArray uFlatArray;
uniform sampler2D uZLight;
uniform sampler2D uColormap;
uniform sampler2DArray uPalette;
uniform sampler2D uSectorLight;
uniform int uPalIndex;
uniform float uViewZ;
uniform float uViewH;
uniform int uExtraLight;
uniform int uFullbright;
uniform int uDynNum;
uniform vec4 uDynPos[8];
uniform vec4 uDynCol[8];
uniform int uBrightmaps;
uniform sampler2D uBrightLut;
out vec4 oColor;
layout(location = 1) out float oIndex;
void main() {
    vec4 t = texelFetch(uFlatArray,
                        ivec3(int(mod(vUv.x, 64.0)),
                              int(mod(vUv.y, 64.0)),
                              int(vFlat + 0.5)), 0);
    int idx = int(t.r * 255.0 + 0.5);
    int lit;
    if (uFullbright != 0) {
        lit = idx;
    } else {
        float dy = abs(uViewH * 0.5 - 0.5 - gl_FragCoord.y)
                   * 200.0 / uViewH;
        float hu = abs(vZ - uViewZ);
        int li = 127;
        if (dy > 1e-6) li = clamp(int(hu * 10.0 / dy), 0, 127);
        // NOTE: base lightnum from the sector-light texture (same
        // value the old baked vertex attr carried: row identical).
        float base = texelFetch(uSectorLight,
                                ivec2(int(vSector + 0.5), 0), 0).r
                     * 255.0;
        int row = clamp(int(base + 0.5) + uExtraLight, 0, 15);
        // NOTE: step 8 brightmaps, same contract as walls (row 15 =
        // brightest ramp).
        int row_use = row;
        if (uBrightmaps != 0
                && texelFetch(uBrightLut, ivec2(idx, 0), 0).r > 0.5)
            row_use = 15;
        int cmap = int(texelFetch(uZLight, ivec2(li, row_use), 0).r
                       * 255.0 + 0.5);
        lit = int(texelFetch(uColormap, ivec2(idx, cmap), 0).r
                  * 255.0 + 0.5);
    }
    // NOTE: v2 dynlights, same contract as walls (uDynNum 0 = vanilla).
    vec3 dyn = vec3(0.0);
    vec3 fragPos = vec3(vWorld, vZ);
    for (int i = 0; i < 8; i++) {
        if (i >= uDynNum) break;
        vec3 ld = uDynPos[i].xyz - fragPos;
        float att = clamp(1.0 - length(ld) / uDynPos[i].w, 0.0, 1.0);
        dyn += uDynCol[i].rgb * (att * uDynCol[i].a);
    }
    oColor = vec4(min(texelFetch(uPalette, ivec3(lit, 0, uPalIndex), 0).rgb + dyn, vec3(1.0)), 1.0);
    // NOTE: index target feeds the fuzz backdrop (vanilla remaps DRAWN
    // pixels through row 6): store lit like the framebuffer holds.
    oIndex = float(lit) / 255.0;
}
"""


PLANE_FRAG_LIN = PLANE_FRAG.replace(
    "    vec4 t = texelFetch(uFlatArray,\n"
    "                        ivec3(int(mod(vUv.x, 64.0)),\n"
    "                              int(mod(vUv.y, 64.0)),\n"
    "                              int(vFlat + 0.5)), 0);",
    "    vec4 t = texture(uFlatArray, vec3((vUv.x + 0.5) / 64.0,\n"
    "                                      (vUv.y + 0.5) / 64.0,\n"
    "                                      float(int(vFlat + 0.5))));",
)
assert PLANE_FRAG_LIN != PLANE_FRAG  # NOTE: base sample text moved on


SPRITE_VERT = """\
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec2 aUv;
layout(location = 2) in float aLight;
uniform mat4 uViewProj;
out vec2 vUv;
out float vLight;
void main() {
    gl_Position = uViewProj * vec4(aPos, 1.0);
    vUv = aUv;
    vLight = aLight;
}
"""

SPRITE_FRAG = """\
#version 330 core
in vec2 vUv;
in float vLight;
uniform sampler2D uSpriteTex;
uniform sampler2D uColormap;
uniform sampler2DArray uPalette;
uniform int uPalIndex;
uniform float uWrap;
uniform float uTexH;
out vec4 oColor;
layout(location = 1) out float oIndex;
void main() {
    vec2 rg = texelFetch(uSpriteTex,
                         ivec2(int(mod(vUv.x, uWrap)),
                               int(mod(vUv.y, uTexH))), 0).rg;
    if (rg.g < 0.5) discard;
    int idx = int(rg.r * 255.0 + 0.5);
    int lit = int(texelFetch(uColormap,
                             ivec2(idx, int(vLight + 0.5)), 0).r
                  * 255.0 + 0.5);
    oColor = vec4(texelFetch(uPalette, ivec3(lit, 0, uPalIndex), 0).rgb, 1.0);
    // NOTE: index target feeds the fuzz backdrop: store lit.
    oIndex = float(lit) / 255.0;
}
"""


SKY_VERT = """\
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in float aU;
uniform mat4 uViewProj;
out float vU;
void main() {
    gl_Position = uViewProj * vec4(aPos, 1.0);
    vU = aU;
}
"""

SKY_FRAG = """\
#version 330 core
in float vU;
uniform sampler2D uSkyTex;
uniform sampler2D uColormap;
uniform sampler2DArray uPalette;
uniform int uPalIndex;
uniform float uViewH;
uniform float uTexH;
out vec4 oColor;
layout(location = 1) out float oIndex;
void main() {
    // NOTE: software sky column (angle>>22, widthmask-folded) and
    // row (SKYTEXTUREMID + (row - cy), iscale 1 texel/row); colormap
    // row 0 like _draw_sky_plane (fullbright-independent).
    vec2 rg = texelFetch(uSkyTex,
                         ivec2(int(mod(vU * 256.0, 256.0)),
                               int(mod(100.0 + (uViewH * 0.5 - 0.5
                                                - gl_FragCoord.y)
                                       * 200.0 / uViewH, uTexH))),
                         0).rg;
    int idx = int(rg.r * 255.0 + 0.5);
    int lit = int(texelFetch(uColormap, ivec2(idx, 0), 0).r
                  * 255.0 + 0.5);
    oColor = vec4(texelFetch(uPalette, ivec3(lit, 0, uPalIndex), 0).rgb, 1.0);
    // NOTE: index target feeds the fuzz backdrop: store lit.
    oIndex = float(lit) / 255.0;
}
"""


FUZZ_FRAG = """\
#version 330 core
in vec2 vUv;
in float vLight;
uniform sampler2D uSpriteTex;
uniform sampler2D uIndexTex;
uniform sampler2D uColormap;
uniform sampler2DArray uPalette;
uniform int uPalIndex;
uniform sampler2D uFuzzTex;
uniform int uFrame;
uniform float uViewH;
uniform float uWrap;
uniform float uTexH;
out vec4 oColor;
void main() {
    // NOTE: silhouette mask first like the software masked posts:
    // transparent texels are skipped, only the monster shape
    // shimmers (otherwise the whole billboard rectangle fuzzes).
    vec2 rg = texelFetch(uSpriteTex,
                         ivec2(int(mod(vUv.x, uWrap)),
                               int(mod(vUv.y, uTexH))), 0).rg;
    if (rg.g < 0.5) discard;
    // NOTE: vanilla fuzz reads the backdrop index one row off
    // (FUZZOFFSETS cycling, 50x1 LUT: PyOpenGL uniform arrays only
    // upload their first element here) through colormap row 6; the
    // software global pixel counter is approximated by frame + row.
    // NOTE: the LUT is R8 (255 for +1, 0 for -1) so the fetch
    // normalizes to 1.0/0.0: decode without the *255 (that mapped
    // +1 to +509, clamping half the rows to the top edge and
    // striping spectres).
    ivec2 px = ivec2(gl_FragCoord.xy);
    int off = int(texelFetch(uFuzzTex,
                             ivec2((uFrame + px.y) % 50, 0), 0).r
                  + 0.5) * 2 - 1;
    int yy = clamp(px.y + off, 0, int(uViewH) - 1);
    int bidx = int(texelFetch(uIndexTex, ivec2(px.x, yy), 0).r
                   * 255.0 + 0.5);
    int lit = int(texelFetch(uColormap, ivec2(bidx, 6), 0).r
                  * 255.0 + 0.5);
    oColor = vec4(texelFetch(uPalette, ivec3(lit, 0, uPalIndex), 0).rgb, 1.0);
}
"""


PSPRITE_VERT = """\
#version 330 core
layout(location = 0) in vec2 aNDC;
layout(location = 1) in vec2 aUv;
out vec2 vUv;
void main() {
    gl_Position = vec4(aNDC, 0.0, 1.0);
    vUv = aUv;
}
"""

PSPRITE_FRAG = """\
#version 330 core
in vec2 vUv;
uniform sampler2D uSpriteTex;
uniform sampler2DArray uPalette;
uniform int uPalIndex;
uniform float uWrap;
uniform float uTexH;
out vec4 oColor;
void main() {
    // NOTE: raw indices like draw_psprite (no colormap, no light:
    // the gun ignores sector darkness, muzzle flash included).
    vec2 rg = texelFetch(uSpriteTex,
                         ivec2(int(mod(vUv.x, uWrap)),
                               int(mod(vUv.y, uTexH))), 0).rg;
    if (rg.g < 0.5) discard;
    int idx = int(rg.r * 255.0 + 0.5);
    oColor = vec4(texelFetch(uPalette, ivec3(idx, 0, uPalIndex), 0).rgb, 1.0);
}
"""


OVERLAY_VERT = """\
#version 330 core
out vec2 vUv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
    vUv = vec2(p.x, 1.0 - p.y);
}
"""

OVERLAY_FRAG = """\
#version 330 core
in vec2 vUv;
uniform sampler2D uOverlay;
uniform sampler2DArray uPalette;
uniform int uPalIndex;
uniform int uFbW;
uniform int uFbH;
out vec4 oColor;
void main() {
    // NOTE: overlay indices are final (software already applied
    // lighting into the fb); the flash palette tints everything
    // uniformly. Index 255 is reserved transparent (verified unused
    // by all overlay art) so menus float over the live world.
    int idx = int(texelFetch(uOverlay,
                             ivec2(int(vUv.x * float(uFbW)),
                                   int(vUv.y * float(uFbH))),
                             0).r * 255.0 + 0.5);
    if (idx == 255) discard;
    oColor = vec4(texelFetch(uPalette, ivec3(idx, 0, uPalIndex), 0)
                  .rgb, 1.0);
}
"""

TEXT_VERT = """\
#version 330 core
layout(location = 0) in vec2 aNDC;
layout(location = 1) in vec2 aUv;
out vec2 vUv;
void main() {
    gl_Position = vec4(aNDC, 0.0, 1.0);
    vUv = aUv;
}
"""


BLOOM_EXTRACT_FRAG = """\
#version 330 core
// NOTE: step 9 bloom-lite (v2 opt-in): threshold the world color
// into a half-res target (the rasterizer downsamples; the source
// FBO color texture is LINEAR in v2 so the fetch smooths).
in vec2 vUv;
uniform sampler2D uSrc;
uniform float uThreshold;
out vec4 oColor;
void main() {
    vec3 c = texture(uSrc, vUv).rgb;
    float lum = dot(c, vec3(0.299, 0.587, 0.114));
    float keep = smoothstep(uThreshold - 0.1, uThreshold + 0.1, lum);
    oColor = vec4(c * keep, 1.0);
}
"""


BLOOM_COMBINE_FRAG = """\
#version 330 core
// NOTE: 9-tap box blur of the extract target, added back over the
// world with ONE,ONE blending (no HDR pipeline at v1: RGB8 clamps
// like software).
in vec2 vUv;
uniform sampler2D uBloom;
uniform vec2 uTexel;
uniform float uStrength;
out vec4 oColor;
void main() {
    vec3 acc = vec3(0.0);
    for (int j = -1; j <= 1; j++)
        for (int i = -1; i <= 1; i++)
            acc += texture(uBloom,
                           vUv + vec2(i, j) * uTexel).rgb;
    oColor = vec4(acc / 9.0 * uStrength, 1.0);
}
"""

TEXT_FRAG = """\
#version 330 core
in vec2 vUv;
uniform sampler2D uTextTex;
out vec4 oColor;
void main() {
    // NOTE: baked per-pixel alpha (surface alpha folded in at
    // upload, like a pygame RGBA blit).
    oColor = texture(uTextTex, vUv);
}
"""

AUTO_VERT = """\
#version 330 core
layout(location = 0) in vec2 aNDC;
layout(location = 1) in vec3 aColor;
out vec3 vColor;
void main() {
    gl_Position = vec4(aNDC, 0.0, 1.0);
    vColor = aColor;
}
"""

AUTO_FRAG = """\
#version 330 core
in vec3 vColor;
out vec4 oColor;
void main() {
    oColor = vec4(vColor, 1.0);
}
"""


def compile_program(vert_src: str, frag_src: str) -> int:
    """Compile + link a program (raises RuntimeError with the info
    log; needs a live context)."""
    from OpenGL import GL
    def compile_one(kind: int, src: str) -> int:
        sh = GL.glCreateShader(kind)
        GL.glShaderSource(sh, src)
        GL.glCompileShader(sh)
        if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
            log = GL.glGetShaderInfoLog(sh)
            if isinstance(log, bytes):
                log = log.decode("utf-8", "replace")
            raise RuntimeError(f"shader compile failed: {log}")
        return sh

    vert = frag = prog = 0
    try:
        vert = compile_one(GL.GL_VERTEX_SHADER, vert_src)
        frag = compile_one(GL.GL_FRAGMENT_SHADER, frag_src)
        prog = GL.glCreateProgram()
        GL.glAttachShader(prog, vert)
        GL.glAttachShader(prog, frag)
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            log = GL.glGetProgramInfoLog(prog)
            if isinstance(log, bytes):
                log = log.decode("utf-8", "replace")
            raise RuntimeError(f"program link failed: {log}")
        return int(prog)
    except Exception:
        if prog:
            GL.glDeleteProgram(prog)
        raise
    finally:
        if vert:
            GL.glDeleteShader(vert)
        if frag:
            GL.glDeleteShader(frag)
