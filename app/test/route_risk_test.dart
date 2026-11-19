// Tests for the safest-time-to-leave math: the hour with the lowest
// summed hourly violations across the route's cameras wins; ties go
// to the earliest hour; cameras without hourly data are skipped.
import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/route_risk.dart';

Prediction _prediction(String cameraId, List<double> hourlyValues) =>
    Prediction(
      cameraId: cameraId,
      intersection: cameraId,
      latitude: null,
      longitude: null,
      date: '2026-10-15',
      timestamp: null,
      dailyPrediction: hourlyValues.fold(0.0, (a, b) => a + b),
      dailySource: 'model',
      riskScore: 0.5,
      riskLevel: 'MEDIUM',
      confidence: 0.9,
      reasons: const [],
      hourly: hourlyValues
          .asMap()
          .entries
          .map(
            (e) => HourlyEntry(
              hour: e.key,
              predictedViolations: e.value,
              band: 'LOW',
              riskScore: 0.01,
              estimate: true,
            ),
          )
          .toList(),
      hourlyEstimateNote: '',
      hourlyMethod: 'model',
      hourlySum: hourlyValues.fold(0.0, (a, b) => a + b),
      reconciliationError: 0,
      modelVersion: 1,
      nearestDistanceM: null,
    );

void main() {
  group('safestDepartureHour', () {
    test('picks the hour with the lowest summed violations', () {
      // Two cameras: both peak at 15:00, quiet at 03:00.
      final preds = {
        'a': _prediction(
          'a',
          List.generate(24, (h) => h == 15 ? 4.0 : (h == 3 ? 0.2 : 1.0)),
        ),
        'b': _prediction(
          'b',
          List.generate(24, (h) => h == 15 ? 3.0 : (h == 3 ? 0.1 : 1.0)),
        ),
      };
      final s = safestDepartureHour(
        predictions: preds,
        cameraIds: ['a', 'b'],
        currentHour: 15,
      );
      expect(s, isNotNull);
      expect(s!.hour, 3);
      expect(s.nowTotal, closeTo(7.0, 0.001));
      expect(s.safestTotal, closeTo(0.3, 0.001));
      expect(s.cameraCount, 2);
    });

    test('ties go to the earliest hour', () {
      final preds = {'a': _prediction('a', List.filled(24, 1.0))};
      final s = safestDepartureHour(
        predictions: preds,
        cameraIds: ['a'],
        currentHour: 10,
      );
      expect(s!.hour, 0);
      expect(s.nowTotal, 1.0);
      expect(s.safestTotal, 1.0);
    });

    test('cameras without hourly data are skipped', () {
      final withHourly = _prediction('a', List.filled(24, 1.0));
      final withoutHourly = Prediction(
        cameraId: 'b',
        intersection: 'b',
        latitude: null,
        longitude: null,
        date: '2026-10-15',
        timestamp: null,
        dailyPrediction: 9,
        dailySource: 'model',
        riskScore: 0.5,
        riskLevel: 'MEDIUM',
        confidence: 0.9,
        reasons: const [],
        hourly: const [],
        hourlyEstimateNote: '',
        hourlyMethod: 'model',
        hourlySum: 9,
        reconciliationError: 0,
        modelVersion: 1,
        nearestDistanceM: null,
      );
      final s = safestDepartureHour(
        predictions: {'a': withHourly, 'b': withoutHourly},
        cameraIds: ['a', 'b'],
        currentHour: 5,
      );
      expect(s, isNotNull);
      expect(s!.cameraCount, 1);
    });

    test('returns null when no camera has hourly data', () {
      expect(
        safestDepartureHour(
          predictions: {},
          cameraIds: ['a', 'b'],
          currentHour: 5,
        ),
        isNull,
      );
    });
  });
}
