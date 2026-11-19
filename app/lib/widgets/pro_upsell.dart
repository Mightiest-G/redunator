// Redunator Pro upsell: the card shown on the Route tab and in Settings
// when the user is not Pro. Lists the store products with their prices
// when Play Console has them; until the products exist in Play Console
// it honestly says subscriptions are not available yet instead of
// showing a dead buy button.
import 'dart:async';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:in_app_purchase/in_app_purchase.dart';

import '../services/sound_service.dart';
import '../services/subscription_service.dart';

class ProUpsellCard extends StatelessWidget {
  const ProUpsellCard({
    super.key,
    required this.subscription,
    required this.sound,
    this.showTitle = true,
  });
  final SubscriptionService subscription;
  final SoundService sound;

  /// False when the card sits under a section header that already
  /// names Pro (Settings), so the title is not printed twice.
  final bool showTitle;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: subscription,
      builder: (context, _) {
        final sub = subscription;
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
                    'Redunator Pro',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                ],
                const Text(
                  'Pro unlocks route planning with risk-coloured roads, '
                  'proximity alerts that warn you as you approach a '
                  'high-risk camera, saved intersections with spike '
                  'alerts, 7- and 30-day risk trends, the safest time '
                  'to leave for a route, a custom alert distance, '
                  'background alerts that keep watching while the app '
                  'is closed, and a 07:00 morning risk digest. '
                  'The map and intersection queries stay free.',
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
                    'Subscriptions are not set up yet — the Pro plans '
                    'still need to be created in Google Play. Check back '
                    'after the next app update.',
                  )
                else
                  ...sub.products.map(
                    (p) => _ProductRow(
                      product: p,
                      subscription: sub,
                      sound: sound,
                    ),
                  ),
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

class _ProductRow extends StatelessWidget {
  const _ProductRow({
    required this.product,
    required this.subscription,
    required this.sound,
  });
  final ProductDetails product;
  final SubscriptionService subscription;
  final SoundService sound;

  String get _label {
    if (product.id == kProYearlyProductId) return 'Pro — yearly';
    return 'Pro — monthly';
  }

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(_label),
      subtitle: Text(product.description),
      trailing: FilledButton(
        onPressed: () async {
          unawaited(sound.playTap());
          await subscription.buy(product);
        },
        child: Text(product.price),
      ),
    );
  }
}
