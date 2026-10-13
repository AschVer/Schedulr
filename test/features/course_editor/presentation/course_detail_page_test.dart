import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:schedulr/features/course_editor/presentation/course_detail_page.dart';
import 'package:schedulr/features/timetable/domain/timetable_models.dart';

void main() {
  testWidgets('显示课程信息、所有安排并触发编辑入口', (tester) async {
    var editCount = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: CourseDetailPage(
          course: _courseWithSessions(),
          periodDefinitions: _periods(),
          onEdit: () async => editCount++,
        ),
      ),
    );

    expect(find.text('操作系统'), findsOneWidget);
    expect(find.textContaining('刘老师'), findsOneWidget);
    expect(find.text('安排 1'), findsOneWidget);
    expect(find.text('安排 2'), findsOneWidget);
    expect(find.textContaining('星期一 第1-2节 · 08:00–09:40'), findsOneWidget);
    expect(find.textContaining('星期三 第5-6节 · 14:00–15:40'), findsOneWidget);
    expect(find.textContaining('第1-4周'), findsOneWidget);
    expect(find.textContaining('第5-8周'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('期末闭卷'),
      300,
      scrollable: find.byType(Scrollable),
    );
    expect(find.text('期末闭卷'), findsOneWidget);

    await tester.tap(find.byKey(const Key('course-detail-edit')));
    await tester.pump();
    expect(editCount, 1);
  });

  testWidgets('三四节课根据楼栋显示错峰下课时间', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: CourseDetailPage(
          course: _flexibleLunchCourse(),
          periodDefinitions: _periods(),
        ),
      ),
    );

    // Building 1: 10:10–11:40
    expect(
      find.textContaining('星期一 第3-4节 · 10:10–11:40'),
      findsOneWidget,
    );
    // Building 13: 10:20–11:50
    expect(
      find.textContaining('星期二 第3-4节 · 10:20–11:50'),
      findsOneWidget,
    );
    // Building 14: 10:30–12:00
    expect(
      find.textContaining('星期三 第3-4节 · 10:30–12:00'),
      findsOneWidget,
    );
    // Playground maps to building 13 slot
    expect(
      find.textContaining('星期四 第3-4节 · 10:20–11:50'),
      findsOneWidget,
    );
  });

  testWidgets('一二节课不受错峰逻辑影响', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: CourseDetailPage(
          course: _fixedPeriodCourse(),
          periodDefinitions: _periods(),
        ),
      ),
    );

    // Period 1-2 in building 1 should still use default times
    expect(
      find.textContaining('星期一 第1-2节 · 08:00–09:40'),
      findsOneWidget,
    );
  });
}

List<PeriodDefinition> _periods() {
  const times = <(String, String)>[
    ('08:00', '08:45'),
    ('08:55', '09:40'),
    ('10:00', '10:45'),
    ('10:55', '11:40'),
    ('14:00', '14:45'),
    ('14:55', '15:40'),
  ];
  return [
    for (var index = 0; index < times.length; index++)
      PeriodDefinition(
        id: 'period-${index + 1}',
        semesterId: 'semester-1',
        period: index + 1,
        startTime: times[index].$1,
        endTime: times[index].$2,
        group: index < 4 ? PeriodGroup.morning : PeriodGroup.afternoon,
      ),
  ];
}

CourseWithSessions _courseWithSessions() {
  const courseId = 'course-detail';
  return CourseWithSessions(
    course: Course(
      id: courseId,
      semesterId: 'semester-1',
      name: '操作系统',
      teacher: '刘老师',
      notes: '期末闭卷',
    ),
    sessions: [
      CourseSession(
        id: 'session-1',
        courseId: courseId,
        weekday: DateTime.monday,
        startPeriod: 1,
        endPeriod: 2,
        location: '计算机楼 101',
        weeks: {1, 2, 3, 4},
      ),
      CourseSession(
        id: 'session-2',
        courseId: courseId,
        weekday: DateTime.wednesday,
        startPeriod: 5,
        endPeriod: 6,
        location: '实验楼 302',
        weeks: {5, 6, 7, 8},
      ),
    ],
  );
}

CourseWithSessions _flexibleLunchCourse() {
  const courseId = 'course-flex';
  return CourseWithSessions(
    course: Course(
      id: courseId,
      semesterId: 'semester-1',
      name: '错峰测试课程',
    ),
    sessions: [
      CourseSession(
        id: 'flex-1',
        courseId: courseId,
        weekday: DateTime.monday,
        startPeriod: 3,
        endPeriod: 4,
        location: '1号楼101教室',
        weeks: {1},
      ),
      CourseSession(
        id: 'flex-2',
        courseId: courseId,
        weekday: DateTime.tuesday,
        startPeriod: 3,
        endPeriod: 4,
        location: '13号楼301教室',
        weeks: {1},
      ),
      CourseSession(
        id: 'flex-3',
        courseId: courseId,
        weekday: DateTime.wednesday,
        startPeriod: 3,
        endPeriod: 4,
        location: '14号楼201教室',
        weeks: {1},
      ),
      CourseSession(
        id: 'flex-4',
        courseId: courseId,
        weekday: DateTime.thursday,
        startPeriod: 3,
        endPeriod: 4,
        location: '操场',
        weeks: {1},
      ),
    ],
  );
}

CourseWithSessions _fixedPeriodCourse() {
  const courseId = 'course-fixed';
  return CourseWithSessions(
    course: Course(
      id: courseId,
      semesterId: 'semester-1',
      name: '固定时段课程',
    ),
    sessions: [
      CourseSession(
        id: 'fixed-1',
        courseId: courseId,
        weekday: DateTime.monday,
        startPeriod: 1,
        endPeriod: 2,
        location: '1号楼101教室',
        weeks: {1},
      ),
    ],
  );
}
