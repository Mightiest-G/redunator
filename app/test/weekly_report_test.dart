// Weekly report (Exclusive): the pure week math and the digest text.
import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/weekly_report.dart';

List<TrendDay> _days(List<double> observed, {String start = '2026-10-01'}) {
  final base = DateTime.parse(start);
  return [
    for (var i = 0; i < observed.length; i++)
      TrendDay(
        date:
            base.add(Duration(days: i)).toIso8601String().substring(0, 10),
        observedViolations: observed[i],
        predictedViolations: observed[i],
      ),
  ];
}

void main() {
  group('weeklySummary', () {
    test('this week vs prior week, percent change and worst day', () {
      // Prior week 2.0/day, this week 4.0/day (worst day 6.0).
      final s = weeklySummary(
        name: 'BELMONT',
        days: _days([
          ...List.filled(7, 2.0),
          4.0,
          4.0,
          6.0,
          4.0,
          3.0,
          3.0,
          4.0,
        ]),
      )!;
      expect(s.thisWeekMean, closeTo(4.0, 1e-9));
      expect(s.priorWeekMean, closeTo(2.0, 1e-9));
      expect(s.percentChange, closeTo(100.0, 1e-9));
      expect(s.worstValue, 6.0);
      expect(s.daysCounted, 7);
      // Spike = >1.5x the prior mean of 2.0, i.e. >3.0: five days
      // qualify (4.0, 4.0, 6.0, 4.0, 4.0).
      expect(s.spikeDays, 5);
    });

    test('no prior week: no baseline, no percent change', () {
      final s = weeklySummary(
        name: 'BELMONT',
        days: _days([1.0, 2.0, 3.0]),
      )!;
      expect(s.thisWeekMean, closeTo(2.0, 1e-9));
      expect(s.priorWeekMean, isNull);
      expect(s.percentChange, isNull);
      expect(s.daysCounted, 3);
    });

    test('no data at all returns null', () {
      expect(weeklySummary(name: 'BELMONT', days: []), isNull);
      final nullDays = _days([0, 0, 0])
          .map(
            (d) => TrendDay(
              date: d.date,
              observedViolations: null,
              predictedViolations: null,
            ),
          )
          .toList();
      expect(weeklySummary(name: 'BELMONT', days: nullDays), isNull);
    });
  });

  group('buildWeeklyDigestText', () {
    test('sorts worst-first, caps at three, shows the change', () {
      final text = buildWeeklyDigestText(
        summaries: [
          weeklySummary(name: 'LOW ST', days: _days([
            ...List.filled(7, 1.0),
            ...List.filled(7, 1.0),
          ]))!,
          weeklySummary(name: 'HIGH ST', days: _days([
            ...List.filled(7, 2.0),
            ...List.filled(7, 8.0),
          ]))!,
          weeklySummary(name: 'MID ST', days: _days([
            ...List.filled(7, 2.0),
            ...List.filled(7, 4.0),
          ]))!,
        ],
      );
      final high = text.indexOf('HIGH ST');
      final mid = text.indexOf('MID ST');
      final low = text.indexOf('LOW ST');
      expect(high, lessThan(mid));
      expect(mid, lessThan(low));
      expect(text, contains('8.0/day'));
      expect(text, contains('+300% vs prior week'));
      expect(text, contains('Daily risk is an estimate.'));
    });

    test('more than three cameras get a "+N more" line', () {
      final summaries = List.generate(
        5,
        (i) => weeklySummary(
          name: 'CAM $i',
          days: _days([...List.filled(7, 1.0), ...List.filled(7, 2.0)]),
        )!,
      );
      final text = buildWeeklyDigestText(summaries: summaries);
      expect(text, contains('…and 2 more saved cameras'));
    });
  });
}
