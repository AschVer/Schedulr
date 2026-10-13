import 'package:flutter_test/flutter_test.dart';
import 'package:schedulr/features/timetable/domain/flexible_period_time.dart';

void main() {
  const resolver = FlexiblePeriodTimeResolver();

  group('extractBuilding', () {
    test('extracts building from "N号楼" format', () {
      expect(FlexiblePeriodTimeResolver.extractBuilding('1号楼101教室'), 1);
      expect(FlexiblePeriodTimeResolver.extractBuilding('4号楼201教室'), 4);
      expect(FlexiblePeriodTimeResolver.extractBuilding('6号楼302教室'), 6);
      expect(FlexiblePeriodTimeResolver.extractBuilding('13号楼301教室'), 13);
      expect(FlexiblePeriodTimeResolver.extractBuilding('14号楼201教室'), 14);
    });

    test('extracts building from campus-prefixed location', () {
      expect(
        FlexiblePeriodTimeResolver.extractBuilding('东坝校区 · 1号楼101教室'),
        1,
      );
      expect(
        FlexiblePeriodTimeResolver.extractBuilding('东坝校区 · 14号楼201教室'),
        14,
      );
    });

    test('extracts building from compact "N-NNNN" format', () {
      expect(FlexiblePeriodTimeResolver.extractBuilding('1-101'), 1);
      expect(FlexiblePeriodTimeResolver.extractBuilding('13-301'), 13);
      expect(FlexiblePeriodTimeResolver.extractBuilding('14-201'), 14);
    });

    test('maps 操场 (playground) to building 13', () {
      expect(FlexiblePeriodTimeResolver.extractBuilding('操场'), 13);
      expect(
        FlexiblePeriodTimeResolver.extractBuilding('东坝校区 · 操场'),
        13,
      );
    });

    test('returns null for unknown formats', () {
      expect(FlexiblePeriodTimeResolver.extractBuilding(null), isNull);
      expect(FlexiblePeriodTimeResolver.extractBuilding(''), isNull);
      expect(
        FlexiblePeriodTimeResolver.extractBuilding('计算机楼 101'),
        isNull,
      );
      expect(FlexiblePeriodTimeResolver.extractBuilding('示例楼 A-101'), isNull);
      expect(
        FlexiblePeriodTimeResolver.extractBuilding('实验楼 302'),
        isNull,
      );
    });

    test('returns null for buildings not in the override map', () {
      expect(FlexiblePeriodTimeResolver.extractBuilding('2号楼101教室'), 2);
      expect(FlexiblePeriodTimeResolver.extractBuilding('3号楼101教室'), 3);
    });
  });

  group('startTimeFor', () {
    test('returns building-specific start for period 3', () {
      expect(resolver.startTimeFor(3, '1号楼101教室'), '10:10');
      expect(resolver.startTimeFor(3, '4号楼201教室'), '10:10');
      expect(resolver.startTimeFor(3, '6号楼302教室'), '10:10');
      expect(resolver.startTimeFor(3, '13号楼301教室'), '10:20');
      expect(resolver.startTimeFor(3, '操场'), '10:20');
      expect(resolver.startTimeFor(3, '14号楼201教室'), '10:30');
    });

    test('returns null for non-flexible periods', () {
      expect(resolver.startTimeFor(1, '1号楼101教室'), isNull);
      expect(resolver.startTimeFor(2, '1号楼101教室'), isNull);
      expect(resolver.startTimeFor(4, '1号楼101教室'), isNull);
      expect(resolver.startTimeFor(5, '1号楼101教室'), isNull);
    });

    test('returns null for unknown buildings', () {
      expect(resolver.startTimeFor(3, '计算机楼 101'), isNull);
      expect(resolver.startTimeFor(3, '2号楼101教室'), isNull);
      expect(resolver.startTimeFor(3, null), isNull);
    });
  });

  group('endTimeFor', () {
    test('returns building-specific end for period 4', () {
      expect(resolver.endTimeFor(4, '1号楼101教室'), '11:40');
      expect(resolver.endTimeFor(4, '4号楼201教室'), '11:40');
      expect(resolver.endTimeFor(4, '6号楼302教室'), '11:40');
      expect(resolver.endTimeFor(4, '13号楼301教室'), '11:50');
      expect(resolver.endTimeFor(4, '操场'), '11:50');
      expect(resolver.endTimeFor(4, '14号楼201教室'), '12:00');
    });

    test('returns null for non-flexible periods', () {
      expect(resolver.endTimeFor(1, '1号楼101教室'), isNull);
      expect(resolver.endTimeFor(3, '1号楼101教室'), isNull);
      expect(resolver.endTimeFor(5, '1号楼101教室'), isNull);
    });

    test('returns null for unknown buildings', () {
      expect(resolver.endTimeFor(4, '计算机楼 101'), isNull);
      expect(resolver.endTimeFor(4, '2号楼101教室'), isNull);
      expect(resolver.endTimeFor(4, null), isNull);
    });
  });

  group('hasOverrideFor', () {
    test('returns true for period 3 with known building', () {
      expect(resolver.hasOverrideFor(3, '1号楼101教室'), isTrue);
      expect(resolver.hasOverrideFor(3, '13号楼301教室'), isTrue);
      expect(resolver.hasOverrideFor(3, '14号楼201教室'), isTrue);
      expect(resolver.hasOverrideFor(3, '操场'), isTrue);
    });

    test('returns true for period 4 with known building', () {
      expect(resolver.hasOverrideFor(4, '1号楼101教室'), isTrue);
      expect(resolver.hasOverrideFor(4, '13号楼301教室'), isTrue);
      expect(resolver.hasOverrideFor(4, '14号楼201教室'), isTrue);
    });

    test('returns false for non-flexible periods', () {
      expect(resolver.hasOverrideFor(1, '1号楼101教室'), isFalse);
      expect(resolver.hasOverrideFor(2, '1号楼101教室'), isFalse);
      expect(resolver.hasOverrideFor(5, '1号楼101教室'), isFalse);
    });

    test('returns false for unknown buildings', () {
      expect(resolver.hasOverrideFor(3, '计算机楼 101'), isFalse);
      expect(resolver.hasOverrideFor(3, '2号楼101教室'), isFalse);
      expect(resolver.hasOverrideFor(4, null), isFalse);
    });
  });

  group('staggered lunch time correctness', () {
    // Verifies the exact time mapping from the BITC Dongba campus schedule.
    test('buildings 1, 4, 6 dismiss at 10:10-11:40', () {
      for (final location in [
        '1号楼101教室',
        '4号楼201教室',
        '6号楼302教室',
      ]) {
        expect(resolver.startTimeFor(3, location), '10:10');
        expect(resolver.endTimeFor(4, location), '11:40');
      }
    });

    test('building 13 and 操场 dismiss at 10:20-11:50', () {
      for (final location in ['13号楼301教室', '操场']) {
        expect(resolver.startTimeFor(3, location), '10:20');
        expect(resolver.endTimeFor(4, location), '11:50');
      }
    });

    test('building 14 dismisses at 10:30-12:00', () {
      const location = '14号楼201教室';
      expect(resolver.startTimeFor(3, location), '10:30');
      expect(resolver.endTimeFor(4, location), '12:00');
    });

    test('periods 1-2 are never overridden', () {
      expect(resolver.startTimeFor(1, '1号楼101教室'), isNull);
      expect(resolver.endTimeFor(2, '1号楼101教室'), isNull);
      expect(resolver.hasOverrideFor(1, '1号楼101教室'), isFalse);
      expect(resolver.hasOverrideFor(2, '1号楼101教室'), isFalse);
    });
  });
}
