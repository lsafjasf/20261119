export const MIN_GAIN = 0;
export const MAX_GAIN = 2;

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function dbToGain(db) {
  return Math.pow(10, db / 20);
}

export function wrapIndex(index, length) {
  if (length <= 0) return 0;
  const wrapped = index % length;
  return wrapped < 0 ? wrapped + length : wrapped;
}

export function readWrappedLinear(source, position) {
  if (source.length === 0) return 0;
  const length = source.length;
  const wrapped = ((position % length) + length) % length;
  const left = Math.floor(wrapped);
  const right = (left + 1) % length;
  const fraction = wrapped - left;
  return source[left] + (source[right] - source[left]) * fraction;
}

export function resampleLinear(source, sourceRate, targetRate) {
  if (!source.length || sourceRate === targetRate) return Float32Array.from(source);
  const outputLength = Math.max(1, Math.round(source.length * targetRate / sourceRate));
  const output = new Float32Array(outputLength);
  const ratio = sourceRate / targetRate;
  for (let i = 0; i < outputLength; i += 1) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(source.length - 1, left + 1);
    const fraction = position - left;
    output[i] = source[left] + (source[right] - source[left]) * fraction;
  }
  return output;
}

export function downmixToMono(audioBuffer) {
  const channels = audioBuffer.numberOfChannels;
  if (channels === 1) return Float32Array.from(audioBuffer.getChannelData(0));
  const mono = new Float32Array(audioBuffer.length);
  for (let channel = 0; channel < channels; channel += 1) {
    const data = audioBuffer.getChannelData(channel);
    for (let i = 0; i < data.length; i += 1) mono[i] += data[i] / channels;
  }
  return mono;
}

export function makeImpulseSource(sampleRate, durationSeconds = 2) {
  const source = new Float32Array(Math.max(1, Math.round(sampleRate * durationSeconds)));
  const impulseFrame = Math.min(source.length - 1, Math.round(source.length * 0.25));
  source[impulseFrame] = 0.9;
  return source;
}

export function makeConstantToneSource(sampleRate, durationSeconds = 2, frequency = 220) {
  const source = new Float32Array(Math.max(1, Math.round(sampleRate * durationSeconds)));
  const angular = 2 * Math.PI * frequency / sampleRate;
  for (let i = 0; i < source.length; i += 1) source[i] = 0.5 * Math.sin(i * angular);
  return source;
}

export function peakEnvelope(source, bucketCount) {
  const buckets = new Float32Array(Math.max(1, bucketCount));
  if (source.length === 0) return buckets;
  const framesPerBucket = source.length / buckets.length;
  for (let bucket = 0; bucket < buckets.length; bucket += 1) {
    const start = Math.floor(bucket * framesPerBucket);
    const end = Math.min(source.length, Math.ceil((bucket + 1) * framesPerBucket));
    let peak = 0;
    for (let i = start; i < end; i += 1) peak = Math.max(peak, Math.abs(source[i]));
    buckets[bucket] = peak;
  }
  return buckets;
}
