// 24-hour risk curve (fl_chart bar chart) with the Phase 1 hourly
// bands: LOW <0.1, MEDIUM 0.1–<0.5, HIGH >=0.5 predicted/hour.
// Every value is labelled as an ESTIMATE (see risk.dart).
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart'; // ignore: deprecated_member_use

import '../api/models.dart';
import '../services/risk.dart';

class HourlyChart extends StatelessWidget {
  const HourlyChart({
    super.key,
    required this.hourly,
    this.selectedHour,
    this.onHourSelected,
  });
  final List<HourlyEntry> hourly;
  final int? selectedHour;
  final ValueChanged<int>? onHourSelected;

  @override
  Widget build(BuildContext context) {
    if (hourly.isEmpty) {
      return const SizedBox(
        height: 120,
        child: Center(child: Text('No hourly data.')),
      );
    }
    final maxY = hourly
        .map((e) => e.predictedViolations)
        .reduce((a, b) => a > b ? a : b);
    final sorted = List<HourlyEntry>.from(hourly)
      ..sort((a, b) => a.hour.compareTo(b.hour));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          height: 180,
          child: BarChart(
            BarChartData(
              maxY: (maxY * 1.15).clamp(0.5, double.infinity),
              barTouchData: BarTouchData(
                touchCallback: (event, response) {
                  if (response?.spot != null && onHourSelected != null) {
                    onHourSelected!(response!.spot!.touchedBarGroupIndex);
                  }
                },
              ),
              titlesData: FlTitlesData(
                leftTitles: AxisTitles(
                  sideTitles: SideTitles(
                    showTitles: true,
                    reservedSize: 32,
                    getTitlesWidget: (v, _) => Text(
                      v.toStringAsFixed(1),
                      style: const TextStyle(fontSize: 10),
                    ),
                  ),
                ),
                bottomTitles: AxisTitles(
                  sideTitles: SideTitles(
                    showTitles: true,
                    getTitlesWidget: (v, _) {
                      final h = v.toInt();
                      if (h % 3 != 0) return const SizedBox.shrink();
                      return Text(
                        '${h.toString().padLeft(2, '0')}:00',
                        style: const TextStyle(fontSize: 10),
                      );
                    },
                  ),
                ),
                topTitles: const AxisTitles(
                  sideTitles: SideTitles(showTitles: false),
                ),
                rightTitles: const AxisTitles(
                  sideTitles: SideTitles(showTitles: false),
                ),
              ),
              gridData: FlGridData(
                drawVerticalLine: false,
                horizontalInterval: (maxY / 4).clamp(0.1, double.infinity),
              ),
              borderData: FlBorderData(show: false),
              barGroups: sorted.map((e) {
                final isSelected = selectedHour == e.hour;
                return BarChartGroupData(
                  x: e.hour,
                  barRods: [
                    BarChartRodData(
                      toY: e.predictedViolations,
                      color: bandColor(e.band),
                      width: isSelected ? 14 : 9,
                      borderRadius: BorderRadius.zero,
                    ),
                  ],
                );
              }).toList(),
            ),
          ),
        ),
        const SizedBox(height: 4),
        const Row(
          children: [
            _LegendDot(Colors.green, 'LOW <0.1/hr'),
            SizedBox(width: 12),
            _LegendDot(Colors.amber, 'MED 0.1–<0.5/hr'),
            SizedBox(width: 12),
            _LegendDot(Colors.red, 'HIGH ≥0.5/hr'),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          hourlyEstimateLabel,
          style: Theme.of(context)
              .textTheme
              .bodySmall
              ?.copyWith(fontStyle: FontStyle.italic),
        ),
      ],
    );
  }
}

class _LegendDot extends StatelessWidget {
  const _LegendDot(this.color, this.label);
  final Color color;
  final String label;
  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 10, height: 10, color: color),
          const SizedBox(width: 4),
          Text(label, style: const TextStyle(fontSize: 11)),
        ],
      );
}
