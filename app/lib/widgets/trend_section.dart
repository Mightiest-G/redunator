// Pro risk-trend section for the intersection detail sheet.
//
// Shows the 7-day and 30-day trend of OBSERVED daily violations (from
// GET /violation_trends, default 90-day window) plus a 30-day
// sparkline of daily observed values. Direction compares the window
// mean against the window before it; a change under 5% reads as flat.
// The free detail sheet keeps its simple "recent daily trend" summary
// above this section — this is the Pro detail.
import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import '../api/models.dart';
import '../services/trend_analysis.dart';

class TrendSection extends StatelessWidget {
  const TrendSection({super.key, required this.trends});

  final ViolationTrends trends;

  @override
  Widget build(BuildContext context) {
    final seven = analyzeTrend(trends.days, 7);
    final thirty = analyzeTrend(trends.days, 30);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _TrendRow(label: '7-day trend', summary: seven),
        const SizedBox(height: 4),
        _TrendRow(label: '30-day trend', summary: thirty),
        const SizedBox(height: 12),
        const Text(
          'Daily observed violations, last 30 days',
          style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 6),
        _Sparkline(values: sparklineValues(trends.days, 30)),
        const SizedBox(height: 4),
        Text(
          'Observed daily counts from the city data (through '
          '${trends.endDate}). Trends compare each window against the '
          'window before it.',
          style: const TextStyle(fontSize: 12, color: Colors.grey),
        ),
      ],
    );
  }
}

class _TrendRow extends StatelessWidget {
  const _TrendRow({required this.label, required this.summary});

  final String label;
  final TrendSummary summary;

  @override
  Widget build(BuildContext context) {
    final IconData icon;
    final Color color;
    switch (summary.direction) {
      case TrendDirection.up:
        icon = Icons.trending_up;
        color = Colors.red;
      case TrendDirection.down:
        icon = Icons.trending_down;
        color = Colors.green;
      case TrendDirection.flat:
        icon = Icons.trending_flat;
        color = Colors.grey;
    }
    final change = summary.hasBaseline && summary.percentChange != null
        ? '${summary.percentChange! >= 0 ? '+' : ''}'
              '${summary.percentChange!.toStringAsFixed(0)}%'
        : 'no baseline';
    return Row(
      children: [
        Icon(icon, color: color, size: 20),
        const SizedBox(width: 8),
        Text(label, style: const TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            '$change '
            '(${summary.recentMean.toStringAsFixed(1)}/day vs '
            '${summary.priorMean.toStringAsFixed(1)}/day before)',
          ),
        ),
      ],
    );
  }
}

class _Sparkline extends StatelessWidget {
  const _Sparkline({required this.values});

  final List<double> values;

  @override
  Widget build(BuildContext context) {
    if (values.isEmpty) {
      return const SizedBox(
        height: 48,
        child: Center(child: Text('No daily data yet.')),
      );
    }
    final maxV = values.reduce((a, b) => a > b ? a : b);
    final color = Theme.of(context).colorScheme.primary;
    return SizedBox(
      height: 48,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: values
            .map(
              (v) => Expanded(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 1),
                  child: Container(
                    height: maxV <= 0 ? 2 : (v / maxV * 44).clamp(2.0, 44.0),
                    color: color,
                  ),
                ),
              ),
            )
            .toList(),
      ),
    );
  }
}
