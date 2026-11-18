// Tap sounds: short UI feedback clicks for buttons, bottom-nav tabs and
// map markers. Reuses the audioplayers setup the proximity alert already
// uses (assets/alert.wav); the tap sound is assets/tap.wav, a ~70 ms
// synthesized click. Audio must never crash the app, so every play is
// guarded and every failure is swallowed — the worst case is silence.
//
// The alert sound is deliberately NOT routed through here: alerts are
// safety-critical and have their own player + test button in Settings.
// This service is only for UI feedback, and the Settings toggle only
// controls these taps.
import 'package:audioplayers/audioplayers.dart';

class SoundService {
  SoundService({this._player, this.enabled = true});

  AudioPlayer? _player;

  /// Whether tap sounds play. Driven by AppSettings.soundEnabled.
  bool enabled;

  /// True once a player has been created (lazy — only on first play).
  /// Tests use this to prove a disabled service never touches audio.
  bool get hasPlayer => _player != null;

  /// Plays the tap click once, if enabled. Never throws.
  Future<void> playTap() async {
    if (!enabled) return;
    try {
      final player = _player ??= AudioPlayer();
      await player.stop();
      await player.play(AssetSource('tap.wav'));
    } catch (_) {
      // Audio must never crash the app; the worst case is silence.
    }
  }

  /// Manual test: plays the tap sound once.
  Future<void> testTap() async => playTap();

  Future<void> dispose() async {
    try {
      await _player?.dispose();
    } catch (_) {
      // Disposal must never crash the app either.
    }
  }
}
