const TARGET_SAMPLE_RATE = 16000;
const OUTPUT_BLOCK_SIZE = 2048;

class PcmTranscriber extends AudioWorkletProcessor {
  constructor() {
    super();
    this.paused = false;
    this.flushing = false;
    this.position = 0;
    this.ratio = sampleRate / TARGET_SAMPLE_RATE;
    this.pending = new Int16Array(OUTPUT_BLOCK_SIZE);
    this.pendingLength = 0;
    this.port.onmessage = (event) => {
      const type = event.data.type;
      if (type === 'pause') this.paused = true;
      if (type === 'resume') this.paused = false;
      if (type === 'flush') this.flushing = true;
    };
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    const frameLength = channel ? channel.length : 128;
    const estimatedLength = Math.ceil(frameLength / this.ratio) + 2;
    const converted = new Float32Array(estimatedLength);
    let convertedLength = 0;

    while (this.position < frameLength) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const first = this.paused || !channel ? 0 : channel[index] || 0;
      const second = this.paused || !channel
        ? 0
        : channel[Math.min(index + 1, frameLength - 1)] || 0;
      converted[convertedLength] = first + (second - first) * fraction;
      convertedLength += 1;
      this.position += this.ratio;
    }
    this.position -= frameLength;

    let offset = 0;
    while (offset < convertedLength) {
      const available = OUTPUT_BLOCK_SIZE - this.pendingLength;
      const count = Math.min(available, convertedLength - offset);
      for (let index = 0; index < count; index += 1) {
        const sample = Math.max(-1, Math.min(1, converted[offset + index]));
        this.pending[this.pendingLength + index] = sample < 0
          ? Math.round(sample * 32768)
          : Math.round(sample * 32767);
      }
      this.pendingLength += count;
      offset += count;
      if (this.pendingLength === OUTPUT_BLOCK_SIZE) this.emitPending();
    }

    if (this.flushing && this.pendingLength > 0) this.emitPending();
    this.flushing = false;
    return true;
  }

  emitPending() {
    const output = this.pending.slice(0, this.pendingLength);
    this.port.postMessage(output.buffer, [output.buffer]);
    this.pendingLength = 0;
  }
}

registerProcessor('pcm-transcriber', PcmTranscriber);
