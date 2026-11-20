// Tap-sound tests: the toggle gates playback, and the toggle itself
// persists. The actual click is judged by ear on the device — these
// tests prove the wiring (no player is even created while disabled,
// and playTap never throws, even with no audio backend in tests).
import 'dart:io';

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

    test('every UI sound plays and follows the toggle', () async {
      // Each function has its own sound (apk-16); all of them go
      // through the same guarded player as the tap.
      final sound = SoundService(enabled: true);
      for (final ui in UiSound.values) {
        await expectLater(sound.playUi(ui), completes);
        await expectLater(sound.testUi(ui), completes);
      }
      await sound.dispose();

      final disabled = SoundService(enabled: false);
      for (final ui in UiSound.values) {
        await disabled.playUi(ui);
      }
      expect(disabled.hasPlayer, isFalse);
      await disabled.dispose();
    });

    test('every alert sound plays and follows the toggle', () async {
      final sound = SoundService(enabled: true);
      for (final alert in AlertSound.values) {
        await expectLater(sound.playAlert(alert), completes);
        await expectLater(sound.testAlert(alert), completes);
      }
      await sound.dispose();

      final disabled = SoundService(enabled: false);
      for (final alert in AlertSound.values) {
        await disabled.playAlert(alert);
      }
      expect(disabled.hasPlayer, isFalse);
      await disabled.dispose();
    });

    test('sound assets are distinct .wav files', () {
      final uiAssets = UiSound.values.map((u) => u.asset).toList();
      final alertAssets = AlertSound.values.map((a) => a.asset).toList();
      expect(uiAssets.toSet().length, uiAssets.length);
      expect(alertAssets.toSet().length, alertAssets.length);
      expect(
        {...uiAssets, ...alertAssets}.length,
        uiAssets.length + alertAssets.length,
      );
      for (final asset in {...uiAssets, ...alertAssets}) {
        expect(asset, endsWith('.wav'));
      }
    });

    test('alert sounds exist in assets/ and res/raw/ identically', () {
      // The in-app player reads assets/; the notification channel
      // reads res/raw/. Both copies must exist and match, or the
      // foreground alert and the background notification would
      // sound different for the same kind.
      for (final alert in AlertSound.values) {
        final inApp = File('assets/${alert.asset}');
        final inRaw = File(
          'android/app/src/main/res/raw/${alert.asset}',
        );
        expect(inApp.existsSync(), isTrue, reason: alert.asset);
        expect(inRaw.existsSync(), isTrue, reason: alert.asset);
        expect(
          inApp.readAsBytesSync(),
          inRaw.readAsBytesSync(),
          reason: alert.asset,
        );
      }
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
