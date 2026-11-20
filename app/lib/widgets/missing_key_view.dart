// Shown INSTEAD of the map when no Google Maps API key is configured.
// The app must never show a silent blank map or crash: this screen
// explains exactly what is missing and how to fix it.
import 'package:flutter/material.dart'; // ignore: deprecated_member_use

class MissingKeyView extends StatelessWidget {
  const MissingKeyView({super.key});

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: const [
        Icon(Icons.map_outlined, size: 64, color: Colors.red),
        SizedBox(height: 16),
        Text(
          'Google Maps API key missing',
          style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
        ),
        SizedBox(height: 12),
        Text(
          'Redunator needs a Google Maps API key to show the map. '
          'Nothing is broken — the key just has not been added yet.',
        ),
        SizedBox(height: 16),
        Text(
          'How to add it',
          style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
        ),
        SizedBox(height: 8),
        Text(
          '1. Go to console.cloud.google.com and open (or create) a project.\n'
          '2. APIs & Services → Library → enable "Maps SDK for Android".\n'
          '   (Also enable "Directions API" if you want the Google '
          'Directions fallback for routing; OSRM routing works without it.)\n'
          '3. APIs & Services → Credentials → Create Credentials → API key.\n'
          '4. Restrict the key: Application restrictions → Android apps, '
          'package com.redunator.redunator; API restrictions → only the '
          'APIs above.\n'
          '5. Give the build the key WITHOUT putting it in the repo:\n'
          '   local: MAPS_API_KEY=<key> flutter build apk --debug\n'
          '   (also pass --dart-define=MAPS_API_KEY=<key> so the Dart\n'
          '   side sees it); CI reads the GOOGLE_MAPS_API_KEY secret.\n'
          '   A key pasted into the repo is exposed the moment the\n'
          '   repo is public — that already happened twice.\n'
          '6. Rebuild the app.\n\n'
          'The Query and Settings tabs work without a key; only '
          'the map and route views need it.',
        ),
      ],
      ),
    );
  }
}
