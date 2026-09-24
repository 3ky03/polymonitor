import { describe, expect, it } from 'vitest';
import { isSoftwareWebGLRenderer } from './webglSupport';

describe('WebGL renderer support', () => {
  it.each([
    'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)))',
    'llvmpipe (LLVM 17.0.6, 256 bits)',
    'Software Rasterizer',
  ])('rejects software renderer %s', (renderer) => {
    expect(isSoftwareWebGLRenderer(renderer)).toBe(true);
  });

  it('accepts a hardware renderer name', () => {
    expect(isSoftwareWebGLRenderer('ANGLE (NVIDIA GeForce RTX 4090 Direct3D11)')).toBe(false);
  });
});
