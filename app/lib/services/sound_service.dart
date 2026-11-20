// Distinct sounds per function and per alert kind (apk-16).
// UI feedback clicks for buttons, bottom-nav tabs and map markers
// live in [UiSound]; safety alerts live in [AlertSound]. Every file
// is a short synthesized WAV (see tools/generate_sounds.py for the
// recipes — no samples, no downloads, fully reproducible).
//
// The two families are deliberately separate:
// - UI sounds play through here, gated by AppSettings.soundEnabled.
// - Alert sounds ALSO play through here when the app is open (the
//   foreground proximity alert, the Settings test buttons), but the
//   background notifications play their sound from the Android
//   notification channel instead — so each alert file exists twice:
//   assets/<name>.wav (in-app playback) and
//   android/app/src/main/res/raw/<name>.wav (channel sound).
//   Keep both copies in sync when a sound is re-tuned.
// Audio must never crash the app, so every play is guarded and
// every failure is swallowed — the worst case is silence.
import 'package:audioplayers/audioplayers.dart';

/// One sound per app function. `tap` is the generic button click
/// everything else falls back to.
enum UiSound {
  tap('tap.wav', 'Button tap'),
  pin('pin.wav', 'Map pin tap'),
  save('save.wav', 'Save / report'),
  query('query.wav', 'Risk query'),
  route('route.wav', 'Route'),
  watch('watch.wav', 'Watch area');

  const UiSound(this.asset, this.label);

  /// Asset filename (assets/); the file lives in pubspec.yaml.
  final String asset;

  /// Plain-language label for the Settings sound-test list.
  final String label;
}

/// One sound per alert kind. The [asset] filename matches the
/// res/raw file of the same name that backs the notification
/// channel for that kind (see local_notifications.dart).
enum AlertSound {
  proximity('proximity_alert.wav', 'High-risk camera nearby'),
  spike('spike_alert.wav', 'Spike in the morning digest'),
  area('area_alert.wav', 'High-risk camera in a watched area'),
  morningDigest('digest_morning.wav', 'Morning digest'),
  weeklyDigest('digest_weekly.wav', 'Weekly digest');

  const AlertSound(this.asset, this.label);

  /// Asset filename (assets/); the res/raw copy shares the name.
  final String asset;

  /// Plain-language label for the Settings sound-test list.
  final String label;
}

class SoundService {
  SoundService({this._player, this.enabled = true});

  AudioPlayer? _player;

  /// Whether sounds play. Driven by AppSettings.soundEnabled.
  bool enabled;

  /// True once a player has been created (lazy — only on first play).
  /// Tests use this to prove a disabled service never touches audio.
  bool get hasPlayer => _player != null;

  Future<void> _play(String asset) async {
    if (!enabled) return;
    try {
      final player = _player ??= AudioPlayer();
      await player.stop();
      await player.play(AssetSource(asset));
    } catch (_) {
      // Audio must never crash the app; the worst case is silence.
    }
  }

  /// Plays the UI sound for [sound] once, if enabled.
  Future<void> playUi(UiSound sound) => _play(sound.asset);

  /// Plays the alert sound for [sound] once, if enabled. Used for
  /// foreground alerts and the Settings test buttons; background
  /// notifications play the same file from their channel instead.
  Future<void> playAlert(AlertSound sound) => _play(sound.asset);

  /// Generic button tap. Kept so existing call sites read plainly.
  Future<void> playTap() => playUi(UiSound.tap);

  /// Per-function conveniences for the mapped call sites.
  Future<void> playPin() => playUi(UiSound.pin);
  Future<void> playSave() => playUi(UiSound.save);
  Future<void> playQuery() => playUi(UiSound.query);
  Future<void> playRoute() => playUi(UiSound.route);
  Future<void> playWatch() => playUi(UiSound.watch);

  /// Manual tests: play one sound once (Settings sound-test list).
  Future<void> testTap() async => playTap();
  Future<void> testUi(UiSound sound) => playUi(sound);
  Future<void> testAlert(AlertSound sound) => playAlert(sound);

  Future<void> dispose() async {
    try {
      await _player?.dispose();
    } catch (_) {
      // Disposal must never crash the app either.
    }
  }
}
