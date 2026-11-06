'use strict';

// One request per worker at a time. The page owns the bounded latest-only queues.
function errorText(error) {
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error);
}

async function decodeJPEG(buffer, offset, length, warnings) {
  const bytes = new Uint8Array(buffer, offset, length);
  if (typeof ImageDecoder === 'function') {
    let decoder = null;
    let image = null;
    try {
      decoder = new ImageDecoder({ data: bytes, type: 'image/jpeg' });
      const result = await decoder.decode({ frameIndex: 0 });
      image = result.image;
      // Keep an independent reference before closing decoder and its decoded frame.
      // The clone is transferred to the main thread, which must close it after draw.
      return { frame: image.clone(), backend: 'ImageDecoder' };
    } catch (error) {
      warnings.push({ stage: 'ImageDecoder', message: errorText(error) });
    } finally {
      if (image) image.close();
      if (decoder) decoder.close();
    }
  }
  if (typeof createImageBitmap !== 'function') {
    throw new Error('Neither usable ImageDecoder nor createImageBitmap is available');
  }
  return {
    frame: await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' })),
    backend: 'createImageBitmap',
  };
}

self.onmessage = async ({ data }) => {
  const { id, serial, buffer, offset, length } = data;
  const warnings = [];
  let frame = null;
  try {
    const result = await decodeJPEG(buffer, offset, length, warnings);
    frame = result.frame;
    self.postMessage({ type: 'decoded', id, serial, frame, backend: result.backend, warnings }, [frame]);
    frame = null; // Ownership moved to the page.
  } catch (error) {
    if (frame) frame.close();
    self.postMessage({ type: 'decode-error', id, serial, message: errorText(error), warnings });
  }
};
