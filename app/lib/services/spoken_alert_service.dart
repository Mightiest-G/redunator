// Spoken alerts (Exclusive): a short voice warning when the
// foreground proximity watcher fires, so the driver hears it without
// looking at the phone. Foreground only — the background WorkManager
// run cannot reliably speak (no audio focus in a background
// isolate), so background alerts stay as notifications + sound.
//
// The voice is the device's default text-to-speech voice. Every
// call is guarded and swallowed: the worst case is silence, never
// a crash.
import 'package:flutter_tts/flutter_tts.dart';

/// Seam over the TTS plugin so tests can use a fake engine.
abstract class TtsEngine {
  Future<void> speak(String text);
  Future<void> stop();
}

class FlutterTtsEngine implements TtsEngine {
  FlutterTtsEngine([FlutterTts? tts]) : _tts = tts ?? FlutterTts();

  final FlutterTts _tts;
  bool _configured = false;

  Future<void> _ensureConfigured() async {
    if (_configured) return;
    try {
      await _tts.setLanguage('en-US');
      await _tts.setSpeechRate(0.95);
      await _tts.setVolume(1.0);
    } catch (_) {
      // Defaults are fine; speaking still works.
    }
    _configured = true;
  }

  @override
  Future<void> speak(String text) async {
    try {
      await _ensureConfigured();
      await _tts.speak(text);
    } catch (_) {
      // Silence, never a crash.
    }
  }

  @override
  Future<void> stop() async {
    try {
      await _tts.stop();
    } catch (_) {}
  }
}

class SpokenAlertService {
  SpokenAlertService({TtsEngine? engine, this.enabled = true})
    : _engine = engine ?? FlutterTtsEngine();

  final TtsEngine _engine;

  /// Whether spoken alerts play. Driven by AppSettings
  /// (spokenAlertsEnabled); Exclusive-only in the UI.
  bool enabled;

  /// True once a speak has been attempted (tests use this to prove
  /// a disabled service never touches TTS).
  bool get hasSpoken => _spoken;

  bool _spoken = false;

  /// The spoken warning for one proximity alert. Distance is
  /// rounded to a friendly number (metres under 1 km).
  static String alertText({
    required String intersection,
    required double distanceM,
  }) {
    final dist = distanceM >= 1000
        ? '${(distanceM / 1000).toStringAsFixed(1)} kilometres'
        : '${distanceM.round()} metres';
    return 'High risk camera ahead: $intersection, $dist away.';
  }

  /// Speaks one proximity alert, if enabled. Never throws.
  Future<void> speakAlert({
    required String intersection,
    required double distanceM,
  }) async {
    if (!enabled) return;
    _spoken = true;
    await _engine.speak(
      alertText(intersection: intersection, distanceM: distanceM),
    );
  }

  /// Manual test from Settings: "Redunator spoken alerts are on."
  Future<void> testSpeak() async {
    if (!enabled) return;
    _spoken = true;
    await _engine.speak('Redunator spoken alerts are on.');
  }

  Future<void> stop() async => _engine.stop();
}
