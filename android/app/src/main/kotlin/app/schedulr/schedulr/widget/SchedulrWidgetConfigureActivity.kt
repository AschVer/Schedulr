package app.schedulr.schedulr.widget

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.widget.ArrayAdapter
import android.widget.ListView
import android.widget.Toast
import app.schedulr.schedulr.R
import es.antonborri.home_widget.HomeWidgetPlugin
import org.json.JSONArray
import org.json.JSONObject

class SchedulrWidgetConfigureActivity : Activity() {
    private var appWidgetId: Int = AppWidgetManager.INVALID_APPWIDGET_ID

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setResult(RESULT_CANCELED)
        appWidgetId = intent.getIntExtra(
            AppWidgetManager.EXTRA_APPWIDGET_ID,
            AppWidgetManager.INVALID_APPWIDGET_ID,
        )
        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish()
            return
        }

        val entries = catalogEntries()
        if (entries.isEmpty()) {
            Toast.makeText(this, "请先在 Schedulr 中创建课表", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        setContentView(R.layout.schedulr_widget_configure)
        // The configure activity uses a NoTitleBar theme and runs edge-to-edge on
        // targetSdk 35+. Apply status/navigation bar insets as padding so the first
        // timetable entry is never drawn underneath a tall cutout status bar.
        applySystemBarInsets(findViewById(R.id.widget_configure_root))

        val labels = entries.map { "${it.name} · ${it.semesterName}" }.toTypedArray()
        val list = findViewById<ListView>(R.id.widget_configure_list).apply {
            adapter = ArrayAdapter(
                this@SchedulrWidgetConfigureActivity,
                R.layout.schedulr_widget_configure_item,
                labels,
            )
            choiceMode = ListView.CHOICE_MODE_SINGLE
            val current = WidgetInstanceStore(this@SchedulrWidgetConfigureActivity)
                .readBinding(appWidgetId)
            val selected = entries.indexOfFirst { it.id == current }
            if (selected >= 0) setItemChecked(selected, true)
            setOnItemClickListener { _, _, position, _ -> finishWith(entries[position].id) }
        }
        list.requestFocus()
    }

    private fun applySystemBarInsets(root: View) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.LOLLIPOP) return
        root.setOnApplyWindowInsetsListener { view, insets ->
            val left: Int
            val top: Int
            val right: Int
            val bottom: Int
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(
                    WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars(),
                )
                left = bars.left
                top = bars.top
                right = bars.right
                bottom = bars.bottom
            } else {
                @Suppress("DEPRECATION")
                left = insets.systemWindowInsetLeft
                @Suppress("DEPRECATION")
                top = insets.systemWindowInsetTop
                @Suppress("DEPRECATION")
                right = insets.systemWindowInsetRight
                @Suppress("DEPRECATION")
                bottom = insets.systemWindowInsetBottom
            }
            view.setPadding(left, top, right, bottom)
            insets
        }
    }

    private fun finishWith(timetableId: String) {
        WidgetInstanceStore(this).writeBinding(appWidgetId, timetableId)
        sendBroadcast(
            Intent(this, SchedulrWidgetReceiver::class.java)
                .setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE)
                .putExtra(
                    AppWidgetManager.EXTRA_APPWIDGET_IDS,
                    intArrayOf(appWidgetId),
                ),
        )
        val result = Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
        setResult(RESULT_OK, result)
        finish()
    }

    private fun catalogEntries(): List<CatalogEntry> {
        val raw = HomeWidgetPlugin.getData(this)
            .getString(WidgetCatalogParser.CATALOG_KEY, null) ?: return emptyList()
        return runCatching {
            val array = JSONObject(raw).optJSONArray("timetables") ?: JSONArray()
            (0 until array.length()).mapNotNull { index ->
                val entry = array.optJSONObject(index) ?: return@mapNotNull null
                val id = entry.optString("timetableId")
                if (id.isBlank()) return@mapNotNull null
                CatalogEntry(
                    id = id,
                    name = entry.optString("timetableName", "课表"),
                    semesterName = entry.optString("semesterName", ""),
                )
            }
        }.getOrDefault(emptyList())
    }

    private data class CatalogEntry(
        val id: String,
        val name: String,
        val semesterName: String,
    )
}
