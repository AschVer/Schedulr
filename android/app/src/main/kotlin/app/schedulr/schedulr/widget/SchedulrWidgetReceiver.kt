package app.schedulr.schedulr.widget

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.RemoteViews
import app.schedulr.schedulr.MainActivity
import app.schedulr.schedulr.R
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetPlugin

class SchedulrWidgetReceiver : AppWidgetProvider() {
    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        val manager = AppWidgetManager.getInstance(context)
        val widgetIds = manager.getAppWidgetIds(ComponentName(context, javaClass))
        // onEnabled can race with the launcher's first onUpdate. If the launcher has
        // already registered the id, render it here so the first add is immediately
        // backed by a local snapshot and a re-armed alarm.
        if (widgetIds.isNotEmpty()) {
            renderAll(context, manager, widgetIds)
        }
    }

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
    ) {
        super.onUpdate(context, appWidgetManager, appWidgetIds)
        renderAll(context, appWidgetManager, appWidgetIds)
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (WidgetLifecyclePolicy.shouldRefreshAll(intent.action)) {
            refreshAll(context)
            return
        }
        // In particular, leave ACTION_APPWIDGET_UPDATE to AppWidgetProvider so
        // the framework dispatches it to onUpdate with the supplied widget ids.
        super.onReceive(context, intent)
    }

    private fun refreshAll(context: Context) {
        val manager = AppWidgetManager.getInstance(context)
        val widgetIds = manager.getAppWidgetIds(ComponentName(context, javaClass))
        if (widgetIds.isEmpty()) {
            cancelRefresh(context)
        } else {
            renderAll(context, manager, widgetIds)
        }
    }

    override fun onAppWidgetOptionsChanged(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetId: Int,
        newOptions: Bundle,
    ) {
        super.onAppWidgetOptionsChanged(context, appWidgetManager, appWidgetId, newOptions)
        // Resizing one instance must not replace a nearer alarm calculated for
        // another instance. Re-rendering all instances also recomputes the
        // global earliest refresh boundary.
        val widgetIds = appWidgetManager.getAppWidgetIds(ComponentName(context, javaClass))
        if (widgetIds.isNotEmpty()) {
            renderAll(context, appWidgetManager, widgetIds, mapOf(appWidgetId to newOptions))
        } else {
            cancelRefresh(context)
        }
    }

    override fun onDeleted(context: Context, appWidgetIds: IntArray) {
        val store = WidgetInstanceStore(context)
        appWidgetIds.forEach(store::deleteBinding)
        super.onDeleted(context, appWidgetIds)
        refreshAll(context)
    }

    override fun onRestored(context: Context, oldWidgetIds: IntArray, newWidgetIds: IntArray) {
        WidgetInstanceStore(context).restoreBindings(oldWidgetIds, newWidgetIds)
        super.onRestored(context, oldWidgetIds, newWidgetIds)
    }

    override fun onDisabled(context: Context) {
        cancelRefresh(context)
        super.onDisabled(context)
    }

    private fun renderAll(
        context: Context,
        manager: AppWidgetManager,
        widgetIds: IntArray,
        optionsByWidgetId: Map<Int, Bundle> = emptyMap(),
    ) {
        var refreshAt: Long? = null
        widgetIds.forEach { widgetId ->
            val snapshot = render(context, manager, widgetId, optionsByWidgetId[widgetId])
            val candidate = WidgetSnapshotParser.nextRefreshMillis(snapshot)
            refreshAt = refreshAt?.let { minOf(it, candidate) } ?: candidate
        }
        refreshAt?.let { scheduleRefresh(context, it) } ?: cancelRefresh(context)
    }

    private fun render(
        context: Context,
        manager: AppWidgetManager,
        widgetId: Int,
        options: Bundle?,
    ): WidgetSnapshot {
        val resolvedOptions = options ?: manager.getAppWidgetOptions(widgetId)
        val plan = WidgetLayoutPolicy.plan(
            resolvedOptions.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0),
            resolvedOptions.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 0),
        )
        val snapshot = try {
            val data = HomeWidgetPlugin.getData(context)
            val binding = WidgetInstanceStore(context).readBinding(widgetId)
            val catalog = data.getString(WidgetCatalogParser.CATALOG_KEY, null)
            val selected = WidgetCatalogParser.select(catalog, binding)
            selected?.snapshot ?: if (binding == null) {
                WidgetSnapshotParser.parse(data.getString(SNAPSHOT_KEY, null))
            } else {
                WidgetSnapshot(state = WidgetSnapshot.STATE_CORRUPT)
            }
        } catch (_: Exception) {
            WidgetSnapshot(state = WidgetSnapshot.STATE_CORRUPT)
        }
        val views = RemoteViews(
            context.packageName,
            if (plan.mode == WidgetLayoutMode.MEDIUM) {
                R.layout.schedulr_widget_medium
            } else {
                R.layout.schedulr_widget_compact
            },
        )
        views.setOnClickPendingIntent(R.id.widget_container, openAppPendingIntent(context, widgetId))
        bindSnapshot(views, snapshot, plan)
        manager.updateAppWidget(widgetId, views)
        return snapshot
    }

    private fun bindSnapshot(views: RemoteViews, snapshot: WidgetSnapshot, plan: WidgetLayoutPlan) {
        views.setTextViewText(R.id.widget_timetable_name, snapshot.timetableName.ifBlank { "Schedulr" })
        views.setTextViewText(
            R.id.widget_date,
            listOf(snapshot.dateLabel, snapshot.weekLabel).filter { it.isNotBlank() }.joinToString(" · ")
                .ifBlank { "课表概览" },
        )

        when {
            snapshot.isCorrupt -> {
                bindHeroMessage(views, "暂时无法读取", "课表数据格式有误", "打开应用后刷新")
                hideRows(views)
            }
            snapshot.state == WidgetSnapshot.STATE_OUTSIDE -> {
                bindHeroMessage(views, "当前不在教学周", "查看学期设置", "点击打开 Schedulr")
                hideRows(views)
            }
            snapshot.isStale -> {
                bindHeroMessage(views, "数据已过期", "请打开应用更新课表", null)
                hideRows(views)
            }
            snapshot.state == WidgetSnapshot.STATE_EMPTY -> {
                bindHeroMessage(views, "暂无课表", "先导入一份课表吧", "点击打开 Schedulr")
                hideRows(views)
            }
            else -> {
                val hero = WidgetDisplayStrategy.hero(snapshot)
                when (hero.source) {
                    WidgetHeroSource.TODAY -> bindHeroCourse(
                        views,
                        if (hero.course?.ongoing == true) "正在上课" else "下一节",
                        hero.course,
                        "今天没有更多课程",
                        "享受今天的空闲时间",
                    )
                    WidgetHeroSource.TOMORROW -> bindHeroCourse(
                        views,
                        "明天第一节",
                        hero.course,
                        "明天没有课程",
                        "好好休息",
                    )
                    WidgetHeroSource.TOMORROW_EMPTY ->
                        bindHeroMessage(views, "明天无课", "好好休息", null)
                    WidgetHeroSource.TOMORROW_OUTSIDE ->
                        bindHeroMessage(views, "明天不在教学周", "好好休息", null)
                    WidgetHeroSource.NONE ->
                        bindHeroMessage(views, "下一节", "今天没有更多课程", "享受今天的空闲时间")
                }
                bindRows(views, snapshot.today, plan.maxRows)
            }
        }
    }

    /** Hero card for status/empty states: no classroom line is shown. */
    private fun bindHeroMessage(
        views: RemoteViews,
        label: String,
        name: String,
        detail: String?,
    ) {
        views.setTextViewText(R.id.widget_next_label, label)
        views.setTextViewText(R.id.widget_next_name, name)
        views.setViewVisibility(R.id.widget_next_location, View.GONE)
        bindSupportLine(views, detail)
    }

    /**
     * Hero card for a real course: course name is the largest text, classroom is
     * the second-largest standalone line, and time/teacher stay in the small line.
     */
    private fun bindHeroCourse(
        views: RemoteViews,
        label: String,
        course: WidgetCourse?,
        emptyName: String,
        emptyDetail: String,
    ) {
        views.setTextViewText(R.id.widget_next_label, label)
        views.setTextViewText(R.id.widget_next_name, course?.name ?: emptyName)
        bindLocation(views, R.id.widget_next_location, course?.location.orEmpty())
        val support = course?.let { heroSupport(it) } ?: emptyDetail
        bindSupportLine(views, support.ifBlank { null })
    }

    /** The classroom line is either bound with text or collapsed, never left stale. */
    private fun bindLocation(views: RemoteViews, viewId: Int, location: String) {
        val trimmed = location.trim()
        if (trimmed.isEmpty()) {
            views.setViewVisibility(viewId, View.GONE)
        } else {
            views.setViewVisibility(viewId, View.VISIBLE)
            views.setTextViewText(viewId, trimmed)
        }
    }

    private fun bindSupportLine(views: RemoteViews, detail: String?) {
        if (detail.isNullOrBlank()) {
            views.setViewVisibility(R.id.widget_next_detail, View.GONE)
        } else {
            views.setViewVisibility(R.id.widget_next_detail, View.VISIBLE)
            views.setTextViewText(R.id.widget_next_detail, detail)
        }
    }

    private fun bindRows(views: RemoteViews, courses: List<WidgetCourse>, maxRows: Int) {
        ROW_IDS.forEachIndexed { index, ids ->
            val visible = index < courses.size && index < maxRows
            views.setViewVisibility(ids.container, if (visible) View.VISIBLE else View.GONE)
            if (!visible) return@forEachIndexed
            val course = courses[index]
            views.setTextViewText(ids.name, course.name)
            bindLocation(views, ids.location, course.location)
            views.setTextViewText(ids.detail, detail(course))
        }
    }

    private fun hideRows(views: RemoteViews) {
        ROW_IDS.forEach { views.setViewVisibility(it.container, View.GONE) }
    }

    /** Small support line under the hero classroom: time and teacher only. */
    private fun heroSupport(course: WidgetCourse): String =
        listOf(timeText(course), course.teacher.takeIf { it.isNotBlank() })
            .filterNotNull().filter { it.isNotBlank() }.joinToString(" · ")

    /** Row support line: time and teacher; the classroom has its own highlighted line. */
    private fun detail(course: WidgetCourse): String =
        listOf(timeText(course), course.teacher.takeIf { it.isNotBlank() })
            .filterNotNull().filter { it.isNotBlank() }.joinToString(" · ").ifBlank { "时间待定" }

    private fun timeText(course: WidgetCourse): String = course.time.ifBlank {
        when {
            course.startPeriod != null && course.endPeriod != null ->
                if (course.startPeriod == course.endPeriod) "第${course.startPeriod}节" else "第${course.startPeriod}–${course.endPeriod}节"
            course.startPeriod != null -> "第${course.startPeriod}节"
            else -> ""
        }
    }

    private fun openAppPendingIntent(context: Context, widgetId: Int): PendingIntent =
        HomeWidgetLaunchIntent.getActivity(
            context,
            MainActivity::class.java,
            Uri.parse("schedulr://home?homeWidget=1&appWidgetId=$widgetId"),
        )

    private data class CourseRowIds(
        val container: Int,
        val name: Int,
        val location: Int,
        val detail: Int,
    )

    companion object {
        const val SNAPSHOT_KEY = "schedulr.widget.snapshot.v1"
        internal const val ACTION_REFRESH = WidgetLifecyclePolicy.ACTION_REFRESH
        private const val REFRESH_REQUEST_CODE = 1042

        /** Both layouts expose the same three rows, each with its own classroom line. */
        private val ROW_IDS = listOf(
            CourseRowIds(
                R.id.widget_course_1,
                R.id.widget_course_name_1,
                R.id.widget_course_location_1,
                R.id.widget_course_detail_1,
            ),
            CourseRowIds(
                R.id.widget_course_2,
                R.id.widget_course_name_2,
                R.id.widget_course_location_2,
                R.id.widget_course_detail_2,
            ),
            CourseRowIds(
                R.id.widget_course_3,
                R.id.widget_course_name_3,
                R.id.widget_course_location_3,
                R.id.widget_course_detail_3,
            ),
        )

        private fun refreshPendingIntent(context: Context): PendingIntent = PendingIntent.getBroadcast(
            context,
            REFRESH_REQUEST_CODE,
            Intent(context, SchedulrWidgetReceiver::class.java).setAction(ACTION_REFRESH),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        private fun scheduleRefresh(context: Context, triggerAtMillis: Long) {
            val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            alarmManager.cancel(refreshPendingIntent(context))
            alarmManager.setAndAllowWhileIdle(
                AlarmManager.RTC,
                triggerAtMillis,
                refreshPendingIntent(context),
            )
        }

        private fun cancelRefresh(context: Context) {
            val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            val pendingIntent = refreshPendingIntent(context)
            alarmManager.cancel(pendingIntent)
            pendingIntent.cancel()
        }
    }
}
