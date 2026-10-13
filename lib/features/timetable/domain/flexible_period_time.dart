/// Resolves building-specific period times for the staggered lunch schedule
/// at BITC Dongba campus (北京信息职业技术学院东坝校区).
///
/// Periods 3-4 use staggered dismissal times by building:
/// - Buildings 1, 4, 6: 10:10–11:40
/// - Building 13 / 操场 (playground): 10:20–11:50
/// - Building 14: 10:30–12:00
///
/// All other periods (1-2, 5-10) use the default [PeriodDefinition] times
/// and are unaffected by this resolver.
class FlexiblePeriodTimeResolver {
  const FlexiblePeriodTimeResolver();

  /// The first period whose start time is building-specific.
  static const int flexibleStartPeriod = 3;

  /// The last period whose end time is building-specific.
  static const int flexibleEndPeriod = 4;

  /// Returns the building-specific start time for [period] given the course
  /// [location], or null when the default [PeriodDefinition] time should be
  /// used.
  String? startTimeFor(int period, String? location) {
    if (period != flexibleStartPeriod) return null;
    final building = extractBuilding(location);
    if (building == null) return null;
    return _buildingOverrides[building]?.blockStart;
  }

  /// Returns the building-specific end time for [period] given the course
  /// [location], or null when the default [PeriodDefinition] time should be
  /// used.
  String? endTimeFor(int period, String? location) {
    if (period != flexibleEndPeriod) return null;
    final building = extractBuilding(location);
    if (building == null) return null;
    return _buildingOverrides[building]?.blockEnd;
  }

  /// Returns true when [period] participates in the flexible lunch schedule
  /// and [location] resolves to a known building override.
  bool hasOverrideFor(int period, String? location) {
    if (period != flexibleStartPeriod && period != flexibleEndPeriod) {
      return false;
    }
    final building = extractBuilding(location);
    return building != null && _buildingOverrides.containsKey(building);
  }

  /// Extracts the BITC building number from a location string.
  ///
  /// Returns the building number as an int (e.g., 1, 4, 6, 13, 14), or null
  /// when no building can be identified. The playground (操场) is mapped to
  /// building 13 because it shares the same staggered slot.
  static int? extractBuilding(String? location) {
    final raw = location?.trim();
    if (raw == null || raw.isEmpty) return null;

    // 操场 (playground) shares the building 13 staggered slot.
    if (raw.contains('操场')) return 13;

    final normalized = raw
        .replaceAll(RegExp(r'[－—–]'), '-')
        .replaceAll(RegExp(r'\s+'), ' ');

    // Match "N号楼" where N is 1-3 digits (e.g., "1号楼", "13号楼").
    final buildingMatch = RegExp(r'(\d{1,3})\s*号楼').firstMatch(normalized);
    if (buildingMatch != null) {
      return int.tryParse(buildingMatch.group(1)!);
    }

    // Match "N-NNNN" compact room format where N is the building number
    // (e.g., "1-101", "13-301").
    final roomMatch = RegExp(r'(\d{1,3})-\d{2,4}').firstMatch(normalized);
    if (roomMatch != null) {
      return int.tryParse(roomMatch.group(1)!);
    }

    return null;
  }

  /// Building-to-time overrides for the 3-4节 staggered lunch block.
  static const Map<int, _BuildingTimeOverride> _buildingOverrides = {
    1: _BuildingTimeOverride(blockStart: '10:10', blockEnd: '11:40'),
    4: _BuildingTimeOverride(blockStart: '10:10', blockEnd: '11:40'),
    6: _BuildingTimeOverride(blockStart: '10:10', blockEnd: '11:40'),
    13: _BuildingTimeOverride(blockStart: '10:20', blockEnd: '11:50'),
    14: _BuildingTimeOverride(blockStart: '10:30', blockEnd: '12:00'),
  };
}

class _BuildingTimeOverride {
  const _BuildingTimeOverride({
    required this.blockStart,
    required this.blockEnd,
  });

  final String blockStart;
  final String blockEnd;
}
