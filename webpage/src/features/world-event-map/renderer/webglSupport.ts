export type WebGLSupport = {
  supported: boolean;
  renderer: string | null;
  reason: string | null;
};

const SOFTWARE_RENDERER_PATTERN = /(swiftshader|llvmpipe|softpipe|software raster|software renderer)/i;

export function isSoftwareWebGLRenderer(renderer: string | null | undefined) {
  return SOFTWARE_RENDERER_PATTERN.test(renderer || '');
}

export function inspectWebGL2Support(options: { allowSoftware?: boolean } = {}): WebGLSupport {
  if (typeof document === 'undefined') {
    return { supported: false, renderer: null, reason: 'WebGL2 requires a browser document.' };
  }
  const canvas = document.createElement('canvas');
  let gl: WebGL2RenderingContext | null = null;
  try {
    gl = canvas.getContext('webgl2', {
      failIfMajorPerformanceCaveat: !options.allowSoftware,
      powerPreference: 'high-performance',
    });
    if (!gl) {
      return {
        supported: false,
        renderer: null,
        reason: 'WebGL2 context creation failed or only a major performance caveat was available.',
      };
    }
    const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = debugInfo
      ? String(gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || '')
      : String(gl.getParameter(gl.RENDERER) || '');
    if (isSoftwareWebGLRenderer(renderer) && !options.allowSoftware) {
      return {
        supported: false,
        renderer,
        reason: `Software WebGL renderer detected: ${renderer}`,
      };
    }
    return { supported: true, renderer: renderer || null, reason: null };
  } catch (error) {
    return {
      supported: false,
      renderer: null,
      reason: error instanceof Error ? error.message : String(error),
    };
  } finally {
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
    canvas.remove();
  }
}
