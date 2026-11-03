import type { AudioInput } from './types';

export async function discoverAudioInputs(): Promise<AudioInput[]> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('当前浏览器无法访问麦克风，请使用最新版 Chrome、Edge 或 Safari。');
  }
  const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  permissionStream.getTracks().forEach((track) => track.stop());
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === 'audioinput')
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label || `音频输入 ${index + 1}`,
    }));
}

export class PcmRecorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;

  async start(deviceId: string, onAudio: (buffer: ArrayBuffer) => void): Promise<void> {
    if (!window.AudioContext) {
      throw new Error('当前浏览器不支持实时音频处理。');
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.context = new AudioContext({ latencyHint: 'interactive' });
    if (!this.context.audioWorklet) {
      this.stopTracks();
      throw new Error('当前浏览器不支持 AudioWorklet，请更换现代浏览器。');
    }
    await this.context.audioWorklet.addModule('/pcm-worklet.js');
    this.source = this.context.createMediaStreamSource(this.stream);
    this.worklet = new AudioWorkletNode(this.context, 'pcm-transcriber', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => onAudio(event.data);
    this.sink = this.context.createGain();
    this.sink.gain.value = 0;
    this.source.connect(this.worklet);
    this.worklet.connect(this.sink);
    this.sink.connect(this.context.destination);
    await this.context.resume();
  }

  setPaused(paused: boolean): void {
    this.worklet?.port.postMessage({ type: paused ? 'pause' : 'resume' });
  }

  async stop(): Promise<void> {
    this.worklet?.port.postMessage({ type: 'flush' });
    await new Promise((resolve) => window.setTimeout(resolve, 60));
    this.source?.disconnect();
    this.worklet?.disconnect();
    this.sink?.disconnect();
    this.worklet?.port.close();
    this.stopTracks();
    if (this.context && this.context.state !== 'closed') {
      await this.context.close();
    }
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.sink = null;
  }

  private stopTracks(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
}
