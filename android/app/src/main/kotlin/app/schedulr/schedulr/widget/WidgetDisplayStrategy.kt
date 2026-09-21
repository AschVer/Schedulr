package app.schedulr.schedulr.widget

/** Pure display decision used by the Android widget receiver. */
enum class WidgetHeroSource {
    TODAY,
    TOMORROW,
    TOMORROW_EMPTY,
    TOMORROW_OUTSIDE,
    NONE,
}

data class WidgetHero(
    val source: WidgetHeroSource,
    val course: WidgetCourse? = null,
)

/** Layout families the receiver can inflate. */
enum class WidgetLayoutMode {
    COMPACT,
    MEDIUM,
}

/** Which layout to inflate plus how many course rows actually fit the slot. */
data class WidgetLayoutPlan(
    val mode: WidgetLayoutMode,
    val maxRows: Int,
)

/**
 * Maps the host-reported slot size to a layout that fits it.
 *
 * The medium layout carries a taller hero block and three full rows, so it may
 * only be used when the slot is both four cells wide and tall enough for that
 * fixed content. A wide-but-short slot — the widget's own 4x2 default — must
 * stay on the flexible compact layout; picking medium there overflows the slot
 * and the host renders the widget as failed/empty.
 */
object WidgetLayoutPolicy {
    /** Four cells at the canonical 70n-30 guideline (4 cells = 250dp). */
    private const val MEDIUM_MIN_WIDTH_DP = 250

    private const val THREE_ROW_MIN_HEIGHT_DP = 290
    private const val TWO_ROW_MIN_HEIGHT_DP = 200

    /** The medium hero block plus three rows need roughly this much height. */
    private const val MEDIUM_MIN_HEIGHT_DP = 340

    fun plan(widthDp: Int, heightDp: Int): WidgetLayoutPlan {
        // Launchers report 0dp before the first layout pass; stay on the layout
        // that is also initialLayout so the very first frame is valid.
        if (widthDp <= 0 && heightDp <= 0) {
            return WidgetLayoutPlan(WidgetLayoutMode.COMPACT, 2)
        }
        if (widthDp >= MEDIUM_MIN_WIDTH_DP && heightDp >= MEDIUM_MIN_HEIGHT_DP) {
            return WidgetLayoutPlan(WidgetLayoutMode.MEDIUM, 3)
        }
        val rows = when {
            heightDp >= THREE_ROW_MIN_HEIGHT_DP -> 3
            heightDp >= TWO_ROW_MIN_HEIGHT_DP -> 2
            else -> 1
        }
        return WidgetLayoutPlan(WidgetLayoutMode.COMPACT, rows)
    }
}

object WidgetDisplayStrategy {
    fun hero(snapshot: WidgetSnapshot): WidgetHero = when {
        snapshot.nextSource == WidgetNextSource.TODAY && snapshot.next != null ->
            WidgetHero(WidgetHeroSource.TODAY, snapshot.next)
        snapshot.nextSource == WidgetNextSource.TOMORROW && snapshot.next != null ->
            WidgetHero(WidgetHeroSource.TOMORROW, snapshot.next)
        snapshot.tomorrowTeachingWeek == null ->
            WidgetHero(WidgetHeroSource.TOMORROW_OUTSIDE)
        snapshot.tomorrow.isEmpty() ->
            WidgetHero(WidgetHeroSource.TOMORROW_EMPTY)
        else -> WidgetHero(WidgetHeroSource.NONE)
    }
}
