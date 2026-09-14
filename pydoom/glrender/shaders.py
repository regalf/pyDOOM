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
    "PLANE_FRAG",
    "PLANE_VERT",
    "WALL_FRAG",
    "WALL_VERT",
    "compile_program",
]

WALL_VERT = """\
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in float aU;
layout(location = 2) in float aTexBase;
layout(location = 3) in float aLight;
layout(location = 4) in vec2 aNormal;
uniform mat4 uViewProj;
out vec2 vUv;
out float vLight;
out vec2 vNormal;
out vec2 vWorld;
void main() {
    gl_Position = uViewProj * vec4(aPos, 1.0);
    vUv = vec2(aU, aTexBase - aPos.z);
    vLight = aLight;
    vNormal = aNormal;
    vWorld = aPos.xy;
}
"""

WALL_FRAG = """\
#version 330 core
in vec2 vUv;
in float vLight;
in vec2 vNormal;
in vec2 vWorld;
uniform sampler2D uWallTex;
uniform sampler2D uScaleLight;
uniform sampler2D uColormap;
uniform sampler2D uPalette;
uniform vec2 uViewPos;
uniform vec2 uViewDir;
uniform int uExtraLight;
uniform int uFullbright;
uniform float uWrap;
uniform float uTexH;
out vec4 oColor;
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
        float den = dot(f, vNormal) * dot(fdir, uViewDir);
        int li = 47;
        if (den > 0.0)
            li = clamp(int(160.0 * dot(fdir, vNormal) / den * 16.0),
                       0, 47);
        int row = clamp(int(vLight + 0.5) + uExtraLight, 0, 15);
        int cmap = int(texelFetch(uScaleLight, ivec2(li, row), 0).r
                       * 255.0 + 0.5);
        lit = int(texelFetch(uColormap, ivec2(idx, cmap), 0).r
                  * 255.0 + 0.5);
    }
    oColor = vec4(texelFetch(uPalette, ivec2(lit, 0), 0).rgb, 1.0);
}
"""

PLANE_VERT = """\
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec2 aUv;
layout(location = 2) in float aFlat;
layout(location = 3) in float aLight;
uniform mat4 uViewProj;
out vec2 vUv;
out float vFlat;
out float vLight;
out float vZ;
void main() {
    gl_Position = uViewProj * vec4(aPos, 1.0);
    vUv = aUv;
    vFlat = aFlat;
    vLight = aLight;
    vZ = aPos.z;
}
"""

PLANE_FRAG = """\
#version 330 core
in vec2 vUv;
in float vFlat;
in float vLight;
in float vZ;
uniform sampler2DArray uFlatArray;
uniform sampler2D uZLight;
uniform sampler2D uColormap;
uniform sampler2D uPalette;
uniform float uViewZ;
uniform float uViewH;
uniform int uExtraLight;
uniform int uFullbright;
out vec4 oColor;
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
        int row = clamp(int(vLight + 0.5) + uExtraLight, 0, 15);
        int cmap = int(texelFetch(uZLight, ivec2(li, row), 0).r
                       * 255.0 + 0.5);
        lit = int(texelFetch(uColormap, ivec2(idx, cmap), 0).r
                  * 255.0 + 0.5);
    }
    oColor = vec4(texelFetch(uPalette, ivec2(lit, 0), 0).rgb, 1.0);
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
