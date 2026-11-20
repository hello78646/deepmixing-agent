# Bundled vision runtime sources

- MediaPipe `@mediapipe/tasks-vision` **0.10.32**, npm distribution (Apache-2.0; see `LICENSE`). `mediapipe.js` is `package/vision_bundle.cjs` renamed without changing the bytes. The WASM loader/binary are the upstream portable non-SIMD pair from `package/wasm`.
- `gesture_recognizer.task` is Google's official Gesture Recognizer float16 model, version 1. No assets or code from the installed Nebula application are included.

Upstream package: `https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-0.10.32.tgz`

Upstream model: `https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task`

SHA-256:

```text
43c65a77825c5d5af7456d4781a64cd15cb3e53f479ff1f923c12b5adecf2b44  mediapipe.js
9f8fc960e363f0fb2f42f7937b97ae9cf9a5630490f71031fa90caa9bb121938  vision_wasm_nosimd_internal.js
924274fcd5ac8985f6570a8573e7971b7bd2d580ba1b8f3beb0ba8f95db6347c  vision_wasm_nosimd_internal.wasm
97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482  gesture_recognizer.task
```

## Workbench runtime

The official gesture model is bundled with the workbench so a clean checkout can run locally without a separate download. The public static showcase still excludes it. The browser processes frames locally and never uploads video; the preview and manual feedback still work if the recognizer is unavailable.
