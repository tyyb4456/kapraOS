import {
  Room,
  RoomEvent,
  Track,
  type DataPacket_Kind,
  type LocalParticipant,
  type Participant,
  type RemoteParticipant,
} from 'livekit-client';

export const VOICE_DATA_TOPIC = 'kapraos.voice';

export interface VoiceControlMessage {
  type: 'approval_pending' | 'reply_done';
  thread_id: string;
  reply?: string;
  actions?: Array<{ name: string; args: Record<string, unknown>; allowed_decisions: string[] }>;
}

export interface VoiceConnectionEvents {
  onAgentJoined: () => void;
  onAgentLeft: () => void;
  onAgentSpeaking: (speaking: boolean) => void;
  onControlMessage: (message: VoiceControlMessage) => void;
  onDisconnected: () => void;
}

function decodeMessage(payload: Uint8Array): VoiceControlMessage | null {
  try {
    const text = new TextDecoder().decode(payload);
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && 'type' in parsed && 'thread_id' in parsed) {
      return parsed as VoiceControlMessage;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Minimal LiveKit voice connection for the KapraOS AI experience.
 *
 * Owns exactly one Room: connects with the backend-minted token, publishes
 * the microphone, plays the agent's audio track, and relays KapraOS control
 * messages (approval notifications worker→frontend, decisions
 * frontend→worker) on the `kapraos.voice` data topic.
 */
export class VoiceConnection {
  private room: Room | null = null;
  private audioElements: HTMLAudioElement[] = [];
  private events: VoiceConnectionEvents | null = null;

  get isConnected(): boolean {
    return this.room?.state === 'connected';
  }

  async connect(serverUrl: string, token: string, events: VoiceConnectionEvents): Promise<void> {
    this.events = events;
    const room = new Room({ adaptiveStream: true, dynacast: true });
    this.room = room;

    room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
      if (track.kind === Track.Kind.Audio && participant?.isAgent) {
        const el = new Audio();
        el.autoplay = true;
        track.attach(el);
        this.audioElements.push(el);
      }
    });

    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) {
        track.detach().forEach((el) => {
          el.pause();
          const idx = this.audioElements.indexOf(el as HTMLAudioElement);
          if (idx >= 0) this.audioElements.splice(idx, 1);
        });
      }
    });

    room.on(RoomEvent.ParticipantConnected, (participant: RemoteParticipant) => {
      if (participant.isAgent) this.events?.onAgentJoined();
    });

    room.on(RoomEvent.ParticipantDisconnected, (participant: RemoteParticipant) => {
      if (participant.isAgent) this.events?.onAgentLeft();
    });

    room.on(RoomEvent.ActiveSpeakersChanged, (speakers: Participant[]) => {
      const agentSpeaking = speakers.some((s) => s.isAgent);
      this.events?.onAgentSpeaking(agentSpeaking);
    });

    room.on(
      RoomEvent.DataReceived,
      (
        payload: Uint8Array,
        _participant?: RemoteParticipant,
        _kind?: DataPacket_Kind,
        topic?: string,
      ) => {
        if (topic !== VOICE_DATA_TOPIC) return;
        const message = decodeMessage(payload);
        if (message) this.events?.onControlMessage(message);
      },
    );

    room.on(RoomEvent.Disconnected, () => {
      this.events?.onDisconnected();
    });

    // Already-joined agent (race with ParticipantConnected) + mic publish.
    await room.connect(serverUrl, token);
    for (const [, participant] of room.remoteParticipants) {
      if ((participant as RemoteParticipant).isAgent) {
        this.events?.onAgentJoined();
        break;
      }
    }
    await room.localParticipant.setMicrophoneEnabled(true);
  }

  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    await this.room?.localParticipant.setMicrophoneEnabled(enabled);
  }

  get localParticipant(): LocalParticipant | undefined {
    return this.room?.localParticipant;
  }

  async sendDecisions(threadId: string, decisions: unknown[]): Promise<void> {
    const room = this.room;
    if (!room || room.state !== 'connected') {
      throw new Error('Voice is not connected.');
    }
    const payload = new TextEncoder().encode(
      JSON.stringify({ type: 'decisions', thread_id: threadId, decisions }),
    );
    await room.localParticipant.publishData(payload, { reliable: true, topic: VOICE_DATA_TOPIC });
  }

  async disconnect(): Promise<void> {
    const room = this.room;
    this.room = null;
    this.events = null;
    for (const el of this.audioElements) {
      try {
        el.pause();
      } catch {
        // ignore
      }
    }
    this.audioElements = [];
    if (room) {
      try {
        await room.disconnect();
      } catch {
        // ignore — already gone
      }
    }
  }
}
