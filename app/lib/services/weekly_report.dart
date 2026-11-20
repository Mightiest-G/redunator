// Weekly risk report (Exclusive): one week in review for each saved
// intersection, from the same GET /violation_trends data the trend
// section already uses (DAILY observed violations, ascending by
// date). Pure math — no new backend calls, no hourly claims.
//
// This week = the last 7 days of data; prior week = the 7 days
// before them. The data lags ~14 days behind today on the city
// feed, so "this week" means the most recent week of data, and the
// UI says so.
import '../api/models.dart';

/// One camera's week in review.
class WeeklyCameraSummary {
  WeeklyCameraSummary({
    required this.name,
    required this.thisWeekMean,
    required this.priorWeekMean,
    required this.percentChange,
    required this.worstDay,
    required this.worstValue,
    required this.spikeDays,
    required this.daysCounted,
  });

  final String name;

  /// Mean observed violations/day over the last 7 days of data.
  final double thisWeekMean;

  /// Mean over the 7 days before them, or null when there is no
  /// earlier data to compare against.
  final double? priorWeekMean;

  /// Percent change of this week vs prior week, or null when the
  /// prior mean is 0 / absent (no usable baseline).
  final double? percentChange;

  /// Date (yyyy-MM-dd) of the worst day this week, or null.
  final String? worstDay;

  /// Observed violations on the worst day, or null.
  final double? worstValue;

  /// Days this week above 1.5x the prior-week mean (the same 1.5x
  /// spike rule as the saved list; falls back to this week's own
  /// mean when there is no prior week).
  final int spikeDays;

  /// Days of data actually present this week (fewer than 7 when the
  /// feed has gaps).
  final int daysCounted;
}

/// Builds one camera's weekly summary. Returns null when there is
/// no usable data at all this week.
WeeklyCameraSummary? weeklySummary({
  required String name,
  required List<TrendDay> days,
}) {
  final values = days
      .map((d) => (d, d.observedViolations ?? d.predictedViolations))
      .where((e) => e.$2 != null)
      .map((e) => (e.$1, e.$2!))
      .toList();
  if (values.isEmpty) return null;
  final recentCount = values.length < 7 ? values.length : 7;
  final recent = values.sublist(values.length - recentCount);
  final priorPool = values.sublist(0, values.length - recentCount);
  final priorCount = priorPool.length < 7 ? priorPool.length : 7;
  final prior = priorCount > 0
      ? priorPool.sublist(priorPool.length - priorCount)
      : <(TrendDay, double)>[];
  double mean(List<(TrendDay, double)> xs) {
    if (xs.isEmpty) return 0;
    var sum = 0.0;
    for (final e in xs) {
      sum += e.$2;
    }
    return sum / xs.length;
  }

  final thisMean = mean(recent);
  final priorMean = prior.isEmpty ? null : mean(prior);
  double? pct;
  if (priorMean != null && priorMean > 0) {
    pct = (thisMean - priorMean) / priorMean * 100;
  }
  var worst = recent.first;
  for (final e in recent) {
    if (e.$2 > worst.$2) worst = e;
  }
  final baseline = priorMean ?? thisMean;
  final spikes = baseline > 0
      ? recent.where((e) => e.$2 > 1.5 * baseline).length
      : 0;
  return WeeklyCameraSummary(
    name: name,
    thisWeekMean: thisMean,
    priorWeekMean: priorMean,
    percentChange: pct,
    worstDay: worst.$1.date,
    worstValue: worst.$2,
    spikeDays: spikes,
    daysCounted: recent.length,
  );
}

/// Sunday-digest body: one line per camera (worst first by this-week
/// mean), plus a header line. Kept short — notification bodies are
/// small.
String buildWeeklyDigestText({
  required List<WeeklyCameraSummary> summaries,
}) {
  final lines = <String>['Your week in risk (most recent data):'];
  final sorted = List<WeeklyCameraSummary>.from(summaries)
    ..sort((a, b) => b.thisWeekMean.compareTo(a.thisWeekMean));
  for (final s in sorted.take(3)) {
    final change = s.percentChange == null
        ? ''
        : s.percentChange! >= 0
            ? ' (+${s.percentChange!.toStringAsFixed(0)}% vs prior week)'
            : ' (${s.percentChange!.toStringAsFixed(0)}% vs prior week)';
    lines.add(
      '${s.name}: ${s.thisWeekMean.toStringAsFixed(1)}/day$change',
    );
  }
  if (sorted.length > 3) {
    lines.add('…and ${sorted.length - 3} more saved cameras');
  }
  lines.add('Daily risk is an estimate.');
  return lines.join('\n');
}
