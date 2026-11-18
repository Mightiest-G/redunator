// Tap-sound tests: the toggle gates playback, and the toggle itself
// persists. The actual click is judged by ear on the device — these
// tests prove the wiring (no player is even created while disabled,
// and playTap never throws, even with no audio backend in tests).
import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/services/settings_service.dart';
import 'package:redunator/services/sound_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  // AudioPlayer touches platform channels (event streams) on creation;
  // a plain test() has no binding, so initialise the test binding here.
  TestWidgetsFlutterBinding.ensureInitialized();

  group('SoundService', () {
    test('disabled: playTap does nothing and creates no player', () async {
      final sound = SoundService(enabled: false);
      await sound.playTap();
      expect(sound.hasPlayer, isFalse);
      await sound.dispose();
    });

    test('enabled then disabled: no player is created while disabled',
        () async {
      final sound = SoundService(enabled: true);
      sound.enabled = false;
      await sound.playTap();
      expect(sound.hasPlayer, isFalse);
      await sound.dispose();
    });

    test('enabled: playTap never throws without an audio backend',
        () async {
      final sound = SoundService(enabled: true);
      // In flutter_test there is no platform audio backend; playTap
      // must swallow that and complete normally.
      await expectLater(sound.playTap(), completes);
      await sound.dispose();
    });

    test('testTap follows the same toggle', () async {
      final sound = SoundService(enabled: false);
      await sound.testTap();
      expect(sound.hasPlayer, isFalse);
      await sound.dispose();
    });
  });

  group('AppSettings sound toggle', () {
    test('defaults on, persists off and back on', () async {
      SharedPreferences.setMockInitialValues({});
      final s = await AppSettings.load();
      expect(s.soundEnabled, isTrue);
      await s.setSoundEnabled(false);
      expect(s.soundEnabled, isFalse);
      final reloaded = await AppSettings.load();
      expect(reloaded.soundEnabled, isFalse);
      await reloaded.setSoundEnabled(true);
      expect((await AppSettings.load()).soundEnabled, isTrue);
    });

    test('withValues carries the toggle', () {
      expect(AppSettings.withValues().soundEnabled, isTrue);
      expect(
        AppSettings.withValues(soundEnabled: false).soundEnabled,
        isFalse,
      );
    });
  });
}
