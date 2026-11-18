import { streamEffectPcm } from "./reverse.js";
import { createWavHeader } from "./wav.js";

export async function exportEffectWav(options) {
  const {
    source,
    format,
    region,
    blockFrames,
    prefetchBlocks,
    suggestedName,
    meter,
    onProgress,
  } = options;
  const header = createWavHeader(format, format.dataLength);

  if ("showSaveFilePicker" in window) {
    let handle;
    try {
      handle = await window.showSaveFilePicker({
        suggestedName,
        types: [{ description: "WAV audio", accept: { "audio/wav": [".wav"] } }],
      });
    } catch (error) {
      if (error?.name === "AbortError") {
        return { cancelled: true, streamed: true, bytes: 0 };
      }
      throw error;
    }

    const writable = await handle.createWritable();
    let bytes = 0;
    try {
      await writable.write(header);
      bytes += header.byteLength;
      for await (const chunk of streamEffectPcm(source, format, {
        region,
        blockFrames,
        prefetchBlocks,
        meter,
      })) {
        await writable.write(chunk);
        bytes += chunk.byteLength;
        onProgress?.(bytes / (format.dataLength + header.byteLength));
      }
      await writable.close();
      return { cancelled: false, streamed: true, bytes };
    } catch (error) {
      await writable.abort().catch(() => {});
      throw error;
    }
  }

  const chunks = [header];
  let bytes = header.byteLength;
  for await (const chunk of streamEffectPcm(source, format, {
    region,
    blockFrames,
    prefetchBlocks,
    meter,
  })) {
    chunks.push(chunk);
    bytes += chunk.byteLength;
    onProgress?.(bytes / (format.dataLength + header.byteLength));
  }

  const blob = new Blob(chunks, { type: "audio/wav" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = suggestedName;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
  return { cancelled: false, streamed: false, bytes };
}

