package com.redunator.redunator

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetProvider

// Home-screen risk widget (Exclusive). The Dart side writes today's
// risk text into the widget data store (WidgetService) and calls
// HomeWidget.updateWidget; this provider renders it. Tapping the
// widget opens the app. No network or location access here — the
// phone already computed the numbers.
class RiskWidgetProvider : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        appWidgetIds.forEach { widgetId ->
            val views = RemoteViews(context.packageName, R.layout.redunator_widget).apply {
                val title = widgetData.getString("redunator_widget_title", null)
                    ?: "Redunator"
                val body = widgetData.getString("redunator_widget_body", null)
                    ?: "Open the app to load today's risk."
                setTextViewText(R.id.widget_title, title)
                setTextViewText(R.id.widget_body, body)
                val intent = Intent(context, MainActivity::class.java)
                val pendingIntent = PendingIntent.getActivity(
                    context,
                    0,
                    intent,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
                setOnClickPendingIntent(R.id.widget_root, pendingIntent)
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }
}
