// Tests for the Pro trend math: window comparisons, the 1.5x spike
// rule and the sparkline slice. TrendDay values are observed daily
// violations, ascending by date.
import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/trend_analysis.dart';

List<TrendDay> _days(List<double> observed) => observed
    .asMap()
    .entries
    .map(
      (e) => TrendDay(
        date: '2026-10-${(e.key + 1).toString().padLeft(2, '0')}',
        observedViolations: e.value,
        predictedViolations: e.value,
      ),
    )
    .toList();

void main() {
  group('analyzeTrend', () {
    test('up when the recent window mean rises more than 5%', () {
      // Prior 7 days at 4/day, last 7 at 5/day (+25%).
      final s = analyzeTrend(
        _days([...List.filled(7, 4.0), ...List.filled(7, 5.0)]),
        7,
      );
      expect(s.direction, TrendDirection.up);
      expect(s.percentChange, closeTo(25, 0.001));
      expect(s.recentMean, 5);
      expect(s.priorMean, 4);
      expect(s.hasBaseline, isTrue);
    });

    test('down when the recent window mean falls more than 5%', () {
      final s = analyzeTrend(
        _days([...List.filled(7, 5.0), ...List.filled(7, 4.0)]),
        7,
      );
      expect(s.direction, TrendDirection.down);
      expect(s.percentChange, closeTo(-20, 0.001));
    });

    test('flat when the change is under 5%', () {
      final s = analyzeTrend(
        _days([...List.filled(7, 4.0), ...List.filled(7, 4.1)]),
        7,
      );
      expect(s.direction, TrendDirection.flat);
      expect(s.percentChange, closeTo(2.5, 0.001));
    });

    test('flat with no baseline when the prior window is all zeros', () {
      final s = analyzeTrend(
        _days([...List.filled(7, 0.0), ...List.filled(7, 3.0)]),
        7,
      );
      expect(s.direction, TrendDirection.up);
      expect(s.percentChange, isNull);
      expect(s.hasBaseline, isFalse);
    });

    test('flat when there is no data at all', () {
      final s = analyzeTrend(_days([]), 7);
      expect(s.direction, TrendDirection.flat);
      expect(s.hasBaseline, isFalse);
    });

    test('30-day window uses the last 30 vs the prior 30', () {
      final s = analyzeTrend(
        _days([...List.filled(30, 2.0), ...List.filled(30, 4.0)]),
        30,
      );
      expect(s.direction, TrendDirection.up);
      expect(s.percentChange, closeTo(100, 0.001));
    });
  });

  group('meanObservedWindow', () {
    test('means the last N days only', () {
      expect(
        meanObservedWindow(_days([1, 2, 3, 4, 5, 6, 7, 8]), 7),
        closeTo(5, 0.001),
      );
    });

    test('returns null with no data', () {
      expect(meanObservedWindow(_days([]), 7), isNull);
    });
  });

  group('isSpike', () {
    test('today above 1.5x the 7-day mean is a spike', () {
      expect(isSpike(7.6, 5.0), isTrue);
    });

    test('today at exactly 1.5x is not a spike', () {
      expect(isSpike(7.5, 5.0), isFalse);
    });

    test('today below 1.5x is not a spike', () {
      expect(isSpike(7.0, 5.0), isFalse);
    });

    test('a zero or missing baseline never spikes', () {
      expect(isSpike(10.0, 0.0), isFalse);
      expect(isSpike(10.0, null), isFalse);
    });
  });

  group('sparklineValues', () {
    test('returns the last N values in order', () {
      expect(sparklineValues(_days([1, 2, 3, 4, 5]), 3), [3.0, 4.0, 5.0]);
    });

    test('returns everything when fewer than N exist', () {
      expect(sparklineValues(_days([1, 2]), 30), [1.0, 2.0]);
    });
  });
}
