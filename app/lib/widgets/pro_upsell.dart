// Redunator plans upsell: the card shown on the Route tab, the
// Saved tab and in Settings when the user is not on the top tier.
// Lists the store products with their prices when Play Console has
// them; until the products exist in Play Console it honestly says
// subscriptions are not available yet instead of showing a dead buy
// button. The current tier is marked "Current plan" instead of
// getting a buy button.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:in_app_purchase/in_app_purchase.dart';

import '../services/sound_service.dart';
import '../services/subscription_service.dart';

/// Backwards-compatible alias (the card used to be Pro-only).
typedef ProUpsellCard = TierUpsellCard;

class TierUpsellCard extends StatelessWidget {
  const TierUpsellCard({
    super.key,
    required this.subscription,
    required this.sound,
    this.showTitle = true,
  });
  final SubscriptionService subscription;
  final SoundService sound;

  /// False when the card sits under a section header that already
  /// names the plans (Settings), so the title is not printed twice.
  final bool showTitle;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: subscription,
      builder: (context, _) {
        final sub = subscription;
        final proProducts = sub.products
            .where((p) => kProProductIds.contains(p.id))
            .toList();
        final exclusiveProducts = sub.products
            .where((p) => kExclusiveProductIds.contains(p.id))
            .toList();
        return Card(
          margin: const EdgeInsets.all(16),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (showTitle) ...[
                  const Text(
                    'Redunator plans',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                ],
                const Text(
                  'Free: the map and intersection queries, plus up to '
                  '3 saved intersections.',
                ),
                const SizedBox(height: 6),
                const Text(
                  'Pro: route planning with risk-coloured roads, '
                  'proximity alerts that warn you as you approach a '
                  'high-risk camera, saved intersections with spike '
                  'alerts (up to 10), 7- and 30-day risk trends, the '
                  'safest time to leave for a route, a custom alert '
                  'distance, background alerts that keep watching '
                  'while the app is closed, and a 07:00 morning risk '
                  'digest.',
                ),
                const SizedBox(height: 6),
                const Text(
                  'Exclusive: everything in Pro, plus unlimited '
                  'saves, area watch (alerts for any high-risk camera '
                  'in a watched neighbourhood), route comparison '
                  'ranked by risk, the weekly risk report with a '
                  'Sunday digest, spoken alerts, and the home-screen '
                  'risk widget.',
                ),
                const SizedBox(height: 12),
                if (!sub.storeAvailable)
                  const Text(
                    'Subscriptions are not available on this device '
                    '(no Play Store billing).',
                  )
                else if (!sub.productsLoaded)
                  const Text('Loading subscription options…')
                else if (sub.products.isEmpty)
                  const Text(
                    'Subscriptions are not set up yet — the plans '
                    'still need to be created in Google Play. Check back '
                    'after the next app update.',
                  )
                else ...[
                  _TierSection(
                    title: 'Pro',
                    products: proProducts,
                    isCurrent: sub.tier == SubscriptionTier.pro,
                    subscription: sub,
                    sound: sound,
                  ),
                  _TierSection(
                    title: 'Exclusive',
                    products: exclusiveProducts,
                    isCurrent: sub.tier == SubscriptionTier.exclusive,
                    subscription: sub,
                    sound: sound,
                  ),
                  if (sub.tier == SubscriptionTier.free)
                    const ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('Free — your current plan'),
                      subtitle: Text(
                        'Map, queries and 3 saved intersections.',
                      ),
                    ),
                ],
                if (sub.lastError != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      sub.lastError!,
                      style: const TextStyle(color: Colors.red),
                    ),
                  ),
                const SizedBox(height: 8),
                TextButton(
                  onPressed: () async {
                    unawaited(sound.playTap());
                    await sub.restore();
                  },
                  child: const Text('Restore purchases'),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _TierSection extends StatelessWidget {
  const _TierSection({
    required this.title,
    required this.products,
    required this.isCurrent,
    required this.subscription,
    required this.sound,
  });

  final String title;
  final List<ProductDetails> products;
  final bool isCurrent;
  final SubscriptionService subscription;
  final SoundService sound;

  @override
  Widget build(BuildContext context) {
    if (products.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        ...products.map(
          (p) => _ProductRow(
            product: p,
            isCurrent: isCurrent,
            subscription: subscription,
            sound: sound,
          ),
        ),
      ],
    );
  }
}

class _ProductRow extends StatelessWidget {
  const _ProductRow({
    required this.product,
    required this.isCurrent,
    required this.subscription,
    required this.sound,
  });
  final ProductDetails product;
  final bool isCurrent;
  final SubscriptionService subscription;
  final SoundService sound;

  String get _label {
    final tier = tierForProduct(product.id);
    final period = product.id.endsWith('_yearly') ? 'yearly' : 'monthly';
    final name = tier == SubscriptionTier.exclusive
        ? 'Exclusive'
        : 'Pro';
    return '$name — $period';
  }

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(_label),
      subtitle: Text(product.description),
      trailing: isCurrent
          ? const Chip(label: Text('Current plan'))
          : FilledButton(
              onPressed: () async {
                unawaited(sound.playTap());
                await subscription.buy(product);
              },
              child: Text(product.price),
            ),
    );
  }
}
