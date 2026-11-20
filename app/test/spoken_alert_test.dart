// Spoken alerts (Exclusive): the text formatting and the
// enabled/disabled gate, against a fake TTS engine.
import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/services/spoken_alert_service.dart';

class _FakeTts implements TtsEngine {
  final List<String> spoken = [];

  @override
  Future<void> speak(String text) async {
    spoken.add(text);
  }

  @override
  Future<void> stop() async {}
}

void main() {
  group('SpokenAlertService.alertText', () {
    test('metres under 1 km, kilometres above', () {
      expect(
        SpokenAlertService.alertText(
          intersection: 'LAKE SHORE DR AND BELMONT',
          distanceM: 250,
        ),
        'High risk camera ahead: LAKE SHORE DR AND BELMONT, 250 metres away.',
      );
      expect(
        SpokenAlertService.alertText(
          intersection: 'LAKE SHORE DR AND BELMONT',
          distanceM: 1500,
        ),
        'High risk camera ahead: LAKE SHORE DR AND BELMONT, 1.5 kilometres away.',
      );
    });
  });

  group('SpokenAlertService speaking', () {
    test('speaks the alert when enabled', () async {
      final tts = _FakeTts();
      final service = SpokenAlertService(engine: tts, enabled: true);
      await service.speakAlert(
        intersection: 'BELMONT',
        distanceM: 250,
      );
      expect(service.hasSpoken, isTrue);
      expect(tts.spoken, [
        'High risk camera ahead: BELMONT, 250 metres away.',
      ]);
    });

    test('a disabled service never touches TTS', () async {
      final tts = _FakeTts();
      final service = SpokenAlertService(engine: tts, enabled: false);
      await service.speakAlert(intersection: 'BELMONT', distanceM: 250);
      await service.testSpeak();
      expect(service.hasSpoken, isFalse);
      expect(tts.spoken, isEmpty);
    });

    test('testSpeak says the test line', () async {
      final tts = _FakeTts();
      final service = SpokenAlertService(engine: tts, enabled: true);
      await service.testSpeak();
      expect(tts.spoken, ['Redunator spoken alerts are on.']);
    });
  });
}
