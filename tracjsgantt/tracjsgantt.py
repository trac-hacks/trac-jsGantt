# -*- coding: utf-8 -*-
#
# Copyright (C) 2010-2014 Chris Nelson <Chris.Nelson@SIXNET.com>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.

import re
import time
from datetime import timedelta, datetime
from operator import itemgetter, attrgetter

from trac.util.datefmt import localtz
try:
    from trac.util.datefmt import to_utimestamp
except ImportError:
    from trac.util.datefmt import to_timestamp as to_utimestamp
from trac.util.text import to_unicode
from trac.util.html import Markup
from trac.wiki.macros import WikiMacroBase
from trac.web.chrome import Chrome
import copy
from trac.ticket.query import Query

from trac.config import IntOption, Option
from trac.core import implements, Component, TracError
from trac.web.api import IRequestFilter
from trac.web.chrome import ITemplateProvider, add_script, add_stylesheet
from pkg_resources import resource_filename

from trac.wiki.api import parse_args

from tracpm import TracPM

try:
    from trac.util.text import javascript_quote
except ImportError:
    # Fallback for Trac<0.11.3 - verbatim copy from Trac 1.0
    _js_quote = {'\\': '\\\\', '"': '\\"', '\b': '\\b', '\f': '\\f',
                 '\n': '\\n', '\r': '\\r', '\t': '\\t', "'": "\\'"}
    for i in range(0x20) + [ord(c) for c in '&<>']:
        _js_quote.setdefault(chr(i), '\\u%04x' % i)
    _js_quote_re = re.compile(r'[\x00-\x1f\\"\b\f\n\r\t\'&<>]')

    def javascript_quote(text):
        """Quote strings for inclusion in javascript"""
        if not text:
            return ''
        def replace(match):
            return _js_quote[match.group(0)]
        return _js_quote_re.sub(replace, text)

# ========================================================================
class TracJSGanttSupport(Component):
    implements(IRequestFilter, ITemplateProvider)

    Option('trac-jsgantt', 'option.format', 'day',
           """Initial format of Gantt chart""")
    Option('trac-jsgantt', 'option.formats', 'day|week|month|quarter',
           """Formats to show for Gantt chart""")
    IntOption('trac-jsgantt', 'option.sample', 0,
              """Show sample Gantt""")
    IntOption('trac-jsgantt', 'option.res', 1,
              """Show resource column""")
    IntOption('trac-jsgantt', 'option.dur', 1,
              """Show duration column""")
    IntOption('trac-jsgantt', 'option.comp', 1,
              """Show percent complete column""")
    Option('trac-jsgantt', 'option.caption', 'Resource',
           """Caption to follow task in Gantt""")
    IntOption('trac-jsgantt', 'option.startDate', 1,
              """Show start date column""")
    IntOption('trac-jsgantt', 'option.endDate', 1,
              """Show finish date column""")
    Option('trac-jsgantt', 'option.dateDisplay', 'mm/dd/yyyy',
           """Format to display dates""")
    IntOption('trac-jsgantt', 'option.openLevel', 999,
              """How many levels of task hierarchy to show open""")
    IntOption('trac-jsgantt', 'option.expandClosedTickets', 1,
              """Show children of closed tasks in the task hierarchy""")
    Option('trac-jsgantt', 'option.colorBy', 'priority',
           """Field to use to color tasks""")
    IntOption('trac-jsgantt', 'option.lwidth', None,
              """Width (in pixels) of left table""")
    IntOption('trac-jsgantt', 'option.showdep', 1,
              """Show dependencies in Gantt""")
    IntOption('trac-jsgantt', 'option.userMap', 1,
              """Map user IDs to user names""")
    IntOption('trac-jsgantt', 'option.omitMilestones', 0,
              """Omit milestones""")
    Option('trac-jsgantt', 'option.schedule', 'alap',
           """Schedule algorithm: alap or asap""")
    IntOption('trac-jsgantt', 'option.doResourceLeveling', 0,
              """Resource level (1) or not (0)""")
    # This seems to be the first floating point option.
    Option('trac-jsgantt', 'option.hoursPerDay', '8.0',
                """Hours worked per day""")
    Option('trac-jsgantt', 'option.display', None,
                """Display filter for tickets in the form 'field1:value1|field2:value2' or 'field:value1|value2'; displays tickets where field1==value1, etc.""")
    Option('trac-jsgantt', 'option.order', 'wbs',
           """Fields to sort tasks by before display.  May include tickets fields (including custom fields) or 'wbs'.""")
    Option('trac-jsgantt', 'option.scrollTo', None,
           """Date to scroll chart to (yyyy-mm--dd or 'today')""")

    Option('trac-jsGantt', 'option.linkStyle', 'standard',
            """Style for ticket links; jsgantt (new window) or standard browser behavior like ticket links.""")


    # ITemplateProvider methods
    def get_htdocs_dirs(self):
        return [('tracjsgantt', resource_filename(__name__, 'htdocs'))]

    def get_templates_dirs(self):
        return []

    # IRequestFilter methods
    def pre_process_request(self, req, handler):
        # I think we should look for a TracJSGantt on the page and set
        # a flag for the post_process_request handler if found
        return handler

    def post_process_request(self, req, template, data, content_type):
        add_script(req, 'tracjsgantt/jsgantt.js')
        add_stylesheet(req, 'tracjsgantt/jsgantt.css')
        add_stylesheet(req, 'tracjsgantt/tracjsgantt.css')
        return template, data, content_type


class TracJSGanttChart(WikiMacroBase):
    """
Displays a Gantt chart for the specified tickets.

The chart display can be controlled with a number of macro arguments:

||'''Argument'''||'''Description'''||'''Default'''||
|| `formats`||What to display in the format control.  A pipe-separated list of `minute`, `hour`, `day`, `week`, `month`, and `quarter` (though `minute` may not be very useful). ||'day|week|month|quarter'||
|| `format`||Initial display format, one of those listed in `formats` || First format ||
|| `sample`||Display sample tasks (1) or not (0) || 0 ||
|| `res`||Show resource column (1) or not (0) || 1 ||
|| `dur`||Show duration colunn (1) or not (0) || 1 ||
|| `comp`||Show percent complete column (1) or not (0) || 1 ||
|| `caption`||Caption to place to right of tasks: None, Caption, Resource, Duration, %Complete || Resource ||
|| `startDate`||Show start date column (1) or not (0) || 1 ||
|| `endDate`||Show end date column (1) or not (0) || 1 ||
|| `dateDisplay`||Date display format: 'mm/dd/yyyy', 'dd/mm/yyyy', or 'yyyy-mm-dd' || 'mm/dd/yyyy' ||
|| `openLevel`||Number of levels of tasks to show.  1 = only top level task.  || 999 ||
|| `colorBy`||Field to use to choose task colors.  Each unique value of the field will have a different color task.  Other likely useful values are owner and milestone but any field can be used. || priority ||
|| `root`||When using something like Subtickets plugin to maintain a tree of tickets and subtickets, you may create a Gantt showing a ticket and all of its descendants with `root=<ticket#>`.  The macro uses the configured `parent` field to find all descendant tasks and build an `id=` argument for Trac's native query handler.[[br]][[br]]Multiple roots may be provided like `root=1|12|32`.[[br]][[br]]When used in a ticket description or comment, `root=self` will display the current ticket's descendants.||None||
|| `goal`||When using something like MasterTickets plugin to maintain ticket dependencies, you may create a Gantt showing a ticket and all of its predecessors with `goal=<ticket#>`.  The macro uses the configured `succ` field to find all predecessor tasks and build an `id=` argument for Trac's native query handler.[[br]][[br]]Multiple goals may be provided like `goal=1|12|32`.[[br]][[br]]When used in a ticket description or comment, `goal=self` will display the current ticket's predecessors.||None||
|| `lwidth`||The width, in pixels, of the table of task names, etc. on the left of the Gantt. || ||
|| `showdep`||Show dependencies (1) or not (0)||1||
|| `userMap`||Map user !IDs to full names (1) or not (0).||1||
|| `omitMilestones`||Show milestones for displayed tickets (0) or only those specified by `milestone=` (1)||0||
|| `schedule`||Schedule tasks based on dependenies and estimates.  Either as soon as possible (asap) or as late as possible (alap)||alap||
||`doResourceLeveling`||Resolve resource conflicts (1) or not (0) when scheduling tickets.||0||
||`display`||Filter for limiting display of tickets.  `owner:fred` shows only tickets owned by fred. `status:closed` shows only closed tickets.||None||
||`order`||Order of fields used to sort tickets before display. `order=milestone` sorts by milestone.  May include ticket fields, including custom fields, or "wbs" (work breakdown structure).||wbs||

Site-wide defaults for macro arguments may be set in the `trac-jsgantt` section of `trac.ini`.  `option.<opt>` overrides the built-in default for `<opt>` from the table above.

All other macro arguments are treated as TracQuery specification (e.g., milestone=ms1|ms2) to control which tickets are displayed.

    """

    pm = None
    options = {}

    # The date part of these formats has to be in sync.  Including
    # hour and minute in the pyDateFormat makes the plugin easier to
    # debug at times because that's how the date shows up in page
    # source.
    #
    # jsDateFormat is the date format that the JavaScript expects
    # dates in.  It can be one of 'mm/dd/yyyy', 'dd/mm/yyyy', or
    # 'yyyy-mm-dd'.  pyDateFormat is a strptime() format that matches
    # jsDateFormat.  As long as they are in sync, there's no real
    # reason to change them.
    jsDateFormat = 'yyyy-mm-dd'
    pyDateFormat = '%Y-%m-%d %H:%M'

    # User map (login -> realname) is loaded on demand, once.
    # Initialization to None means it is not yet initialized.
    user_map = None

    def __init__(self):
        # Instantiate the PM component
        self.pm = TracPM(self.env)

        self.GanttID = 'g'


        # All the macro's options with default values.
        # Anything else passed to the macro is a TracQuery field.
        options = ('format', 'formats', 'sample', 'res', 'dur', 'comp',
                   'caption', 'startDate', 'endDate', 'dateDisplay',
                   'openLevel', 'expandClosedTickets', 'colorBy', 'lwidth',
                   'showdep', 'userMap', 'omitMilestones',
                   'schedule', 'hoursPerDay', 'doResourceLeveling',
                   'display', 'order', 'scrollTo', 'linkStyle')

        for opt in options:
            self.options[opt] = self.config.get('trac-jsgantt',
                                                'option.%s' % opt)


    def _begin_gantt(self, options):
        if options['format']:
            defaultFormat = options['format']
        else:
            defaultFormat = options['formats'].split('|')[0]
        showdep = options['showdep']
        text = ''
        text += '<div style="position:relative" class="gantt" ' + \
            'id="GanttChartDIV_'+self.GanttID+'"></div>\n'
        text += '<script language="javascript">\n'
        text += 'var '+self.GanttID+' = new JSGantt.GanttChart("'+ \
            self.GanttID+'",document.getElementById("GanttChartDIV_'+ \
            self.GanttID+'"), "%s", "%s");\n' % \
            (javascript_quote(defaultFormat), showdep)
        text += 'var t;\n'
        text += 'if (window.addEventListener){\n'
        text += '  window.addEventListener("resize", ' + \
            'function() { ' + self.GanttID+'.Draw(); '
        if options['showdep']:
            text += self.GanttID+'.DrawDependencies();'
        text += '}, false);\n'
        text += '} else {\n'
        text += '  window.attachEvent("onresize", ' + \
            'function() { '+self.GanttID+'.Draw(); '
        if options['showdep']:
            text += self.GanttID+'.DrawDependencies();'
        text += '});\n'
        text += '}\n'
        return text

    def _end_gantt(self, options):
        chart = ''
        chart += self.GanttID+'.Draw();\n'
        if options['showdep']:
            chart += self.GanttID+'.DrawDependencies();\n'
        chart += '</script>\n'
        return chart

    def _gantt_options(self, options):
        opt = ''
        if (options['linkStyle']):
            linkStyle = options['linkStyle']
        else:
            linkStyle = 'standard'
        opt += self.GanttID+'.setLinkStyle("%s")\n' % linkStyle
        opt += self.GanttID+'.setShowRes(%s);\n' % options['res']
        opt += self.GanttID+'.setShowDur(%s);\n' % options['dur']
        opt += self.GanttID+'.setShowComp(%s);\n' % options['comp']
        if (options['scrollTo']):
            opt += self.GanttID+'.setScrollDate("%s");\n' % options['scrollTo']
        w = options['lwidth']
        if w:
            opt += self.GanttID+'.setLeftWidth(%s);\n' % w


        opt += self.GanttID+'.setCaptionType("%s");\n' % \
            javascript_quote(options['caption'])

        opt += self.GanttID+'.setShowStartDate(%s);\n' % options['startDate']
        opt += self.GanttID+'.setShowEndDate(%s);\n' % options['endDate']

        opt += self.GanttID+'.setDateInputFormat("%s");\n' % \
            javascript_quote(self.jsDateFormat)

        opt += self.GanttID+'.setDateDisplayFormat("%s");\n' % \
            javascript_quote(options['dateDisplay'])

        opt += self.GanttID+'.setFormatArr(%s);\n' % ','.join(
            '"%s"' % javascript_quote(f) for f in options['formats'].split('|'))
        opt += self.GanttID+'.setPopupFeatures("location=1,scrollbars=1");\n'
        return opt

    # TODO - use ticket-classN styles instead of colors?
    def _add_sample_tasks(self):
        task= ''
        tasks = self.GanttID+'.setDateInputFormat("mm/dd/yyyy");\n'

        #                                                                         ID    Name                   Start        End          Display    Link                    MS Res         Pct  Gr Par Open Dep Cap
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',1,   "Define Chart API",     "",          "",          "#ff0000", "http://help.com",      0, "Brian",     0,  1, 0,  1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',11,  "Chart Object",         "2/20/2011", "2/20/2011", "#ff00ff", "http://www.yahoo.com", 1, "Shlomy",  100,  0, 1,  1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',12,  "Task Objects",         "",          "",          "#00ff00", "",                     0, "Shlomy",   40,  1, 1,  1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',121, "Constructor Proc",     "2/21/2011", "3/9/2011",  "#00ffff", "http://www.yahoo.com", 0, "Brian T.", 60,  0, 12, 1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',122, "Task Variables",       "3/6/2011",  "3/11/2011", "#ff0000", "http://help.com",      0, "",         60,  0, 12, 1,121));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',123, "Task Functions",       "3/9/2011",  "3/29/2011", "#ff0000", "http://help.com",      0, "Anyone",   60,  0, 12, 1, 0, "This is another caption"));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',2,   "Create HTML Shell",    "3/24/2011", "3/25/2011", "#ffff00", "http://help.com",      0, "Brian",    20,  0, 0,  1,122));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',3,   "Code Javascript",      "",          "",          "#ff0000", "http://help.com",      0, "Brian",     0,  1, 0,  1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',31,  "Define Variables",     "2/25/2011", "3/17/2011", "#ff00ff", "http://help.com",      0, "Brian",    30,  0, 3,  1, 0,"Caption 1"));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',32,  "Calculate Chart Size", "3/15/2011", "3/24/2011", "#00ff00", "http://help.com",      0, "Shlomy",   40,  0, 3,  1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',33,  "Draw Taks Items",      "",          "",          "#00ff00", "http://help.com",      0, "Someone",  40,  1, 3,  1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',332, "Task Label Table",     "3/6/2011",  "3/11/2011", "#0000ff", "http://help.com",      0, "Brian",    60,  0, 33, 1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',333, "Task Scrolling Grid",  "3/9/2011",  "3/20/2011", "#0000ff", "http://help.com",      0, "Brian",    60,  0, 33, 1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',34,  "Draw Task Bars",       "",          "",          "#990000", "http://help.com",      0, "Anybody",  60,  1, 3,  1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',341, "Loop each Task",       "3/26/2011", "4/11/2011", "#ff0000", "http://help.com",      0, "Brian",    60,  0, 34, 1, "332,333"));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',342, "Calculate Start/Stop", "4/12/2011", "5/18/2011", "#ff6666", "http://help.com",      0, "Brian",    60,  0, 34, 1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',343, "Draw Task Div",        "5/13/2011", "5/17/2011", "#ff0000", "http://help.com",      0, "Brian",    60,  0, 34, 1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',344, "Draw Completion Div",  "5/17/2011", "6/04/2011", "#ff0000", "http://help.com",      0, "Brian",    60,  0, 34, 1));\n'
        tasks += self.GanttID+'.AddTaskItem(new JSGantt.TaskItem('+self.GanttID+',35,  "Make Updates",         "10/17/2011","12/04/2011","#f600f6", "http://help.com",      0, "Brian",    30,  0, 3,  1));\n'
        return tasks

    # Get the required columns for the tickets which match the
    # criteria in options.
    def _query_tickets(self, options):
        query_options = {}
        for key in options.keys():
            if not key in self.options:
                query_options[key] = options[key]

        # The fields always needed by the Gantt
        fields = set([
            'description',
            'owner',
            'type',
            'status',
            'summary',
            'milestone',
            'priority'])

        # Make sure the coloring field is included
        if 'colorBy' in options:
            fields.add(str(options['colorBy']))

        rawtickets = self.pm.query(query_options, fields, self.req)

        # Do permissions check on tickets
        tickets = [t for t in rawtickets
                   if 'TICKET_VIEW' in self.req.perm('ticket', t['id'])]

        return tickets

    def _compare_tickets(self, t1, t2):
        # If t2 depends on t1, t2 is first
        if t1['id'] in self.pm.successors(t2):
            return 1
        # If t1 depends on t2, t1 is first
        elif t2['id'] in self.pm.successors(t1):
            return -1
        # If t1 ends first, it's first
        elif self.pm.finish(t1) < self.pm.finish(t2):
            return -1
        # If t2 ends first, it's first
        elif self.pm.finish(t1) > self.pm.finish(t2):
            return 1
        # End dates are same. If t1 starts later, it's later
        elif self.pm.start(t1) > self.pm.start(t2):
            return 1
        # Otherwise, preserve order (assume t1 is before t2 when called)
        else:
            return 0

    # Compute WBS for sorting and figure out the tickets' levels for
    # controlling how many levels are open.
    #
    # WBS is a list like [ 2, 4, 1] (the first child of the fourth
    # child of the second top-level element).
    def _compute_wbs(self):
        # Set the ticket's level and wbs then recurse to children.
        def _setLevel(tid, wbs, level):
            # Update this node
            self.ticketsByID[tid]['level'] = level
            self.ticketsByID[tid]['wbs'] = copy.copy(wbs)

            # Recurse to children
            childIDs = self.pm.children(self.ticketsByID[tid])
            if childIDs:
                childTickets = [self.ticketsByID[cid] for cid in childIDs]
                childTickets.sort(self._compare_tickets)
                childIDs = [ct['id'] for ct in childTickets]

                # Add another level
                wbs.append(1)
                for c in childIDs:
                    wbs = _setLevel(c, wbs, level+1)
                # Remove the level we added
                wbs.pop()


            # Increment last element of wbs
            wbs[len(wbs)-1] += 1

            return wbs

        # Set WBS and level on all top level tickets (and recurse) If
        # a ticket's parent is not in the viewed tickets, consider it
        # top-level
        wbs = [ 1 ]
        roots = self.pm.roots(self.ticketsByID)
        for t in self.tickets:
            if t['id'] in roots:
                wbs = _setLevel(t['id'], wbs, 1)


    def _task_display(self, t, options):
        def _buildMap(field):
            self.classMap = {}
            i = 0
            for t in self.tickets:
                if t[field] not in self.classMap:
                    i = i + 1
                    self.classMap[t[field]] = i

        def _buildEnumMap(field):
            self.classMap = {}
            db = self.env.get_db_cnx()
            cursor = db.cursor()
            cursor.execute("SELECT name," +
                           db.cast('value', 'int') +
                           " FROM enum WHERE type=%s", (field,))
            for name, value in cursor:
                self.classMap[name] = value

        display = None
        colorBy = options['colorBy']

        # Build the map the first time we need it
        if self.classMap == None:
            # Enums (TODO: what others should I list?)
            if options['colorBy'] in ['priority', 'severity']:
                _buildEnumMap(colorBy)
            else:
                _buildMap(colorBy)

        # Set display based on class map
        if t[colorBy] in self.classMap:
            display = 'class=ticket-class%d' % self.classMap[t[colorBy]]

        # Add closed status for strike through
        if t['status'] == 'closed':
            if display == None:
                display = 'class=ticket-closed'
            else:
                display += ' ticket-closed'

        if display == None:
            display = '#ff7f3f'
        return display


    # Format a ticket into JavaScript source to display the
    # task. ticket is expected to have:
    #   children - child ticket IDs or None
    #   description - ticket description.
    #   id - ticket ID, an integer
    #   level - levels from root (0)
    #   link - What to link to
    #   owner - Used as resource name.
    #   percent - integer percent complete, 0..100 (or "act/est")
    #   priority - used to color the task
    #   calc_finish - end date (ignored if children is not None)
    #   self.fields[parent] - parent ticket ID
    #   self.fields[pred] - predecessor ticket IDs
    #   calc_start - start date (ignored if children is not None)
    #   status - string displayed in tool tip ; FIXME - not displayed yet
    #   summary - ticket summary
    #   type - string displayed in tool tip FIXME - not displayed yet
    def _format_ticket(self, ticket, options):
        # Translate owner to full name
        def _owner(ticket):
            if self.pm.isMilestone(ticket):
                owner_name = ''
            else:
                owner_name = ticket['owner']
                if options['userMap']:
                    # Build the map the first time we use it
                    if self.user_map is None:
                        self.user_map = {}
                        for username, name, email in self.env.get_known_users():
                            self.user_map[username] = name
                    # Map the user name
                    if self.user_map.get(owner_name):
                        owner_name = self.user_map[owner_name]
            return owner_name

        task = ''

        # pID, pName
        if self.pm.isMilestone(ticket):
            if ticket['id'] > 0:
                # Put ID number on inchpebbles
                name = 'MS:%s (#%s)' % (ticket['summary'], ticket['id'])
            else:
                # Don't show bogus ID of milestone pseudo tickets.
                name = 'MS:%s' % ticket['summary']
        else:
            name = "#%d:%s (%s %s)" % \
                   (ticket['id'], ticket['summary'],
                    ticket['status'], ticket['type'])
        task += 't = new JSGantt.TaskItem(%s,%d,"%s",' % \
            (self.GanttID, ticket['id'], javascript_quote(name))

        # pStart, pEnd
        task += '"%s",' % self.pm.start(ticket).strftime(self.pyDateFormat)
        task += '"%s",' % self.pm.finish(ticket).strftime(self.pyDateFormat)

        # pDisplay
        task += '"%s",' % javascript_quote(self._task_display(ticket, options))

        # pLink
        task += '"%s",' % javascript_quote(ticket['link'])

        # pMile
        if self.pm.isMilestone(ticket):
            task += '1,'
        else:
            task += '0,'

        # pRes (owner)
        task += '"%s",' % javascript_quote(_owner(ticket))

        # pComp (percent complete); integer 0..100
        task += '"%s",' % self.pm.percentComplete(ticket)

        # pGroup (has children)
        if self.pm.children(ticket):
            task += '%s,' % 1
        else:
            task += '%s,' % 0

        # pParent (parent task ID)
        # If there's no parent, don't link to it
        if self.pm.parent(ticket) == None:
            task += '%s,' % 0
        else:
            task += '%s,' % self.pm.parent(ticket)

        # open
        if int(ticket['level']) < int(options['openLevel']) and \
                ((options['expandClosedTickets'] != 0) or \
                     (ticket['status'] != 'closed')):
            openGroup = 1
        else:
            openGroup = 0
        task += '%d,' % openGroup

        # predecessors
        pred = [str(s) for s in self.pm.predecessors(ticket)]
        if len(pred):
            task += '"%s",' % javascript_quote(','.join(pred))
        else:
            task += '"%s",' % javascript_quote(','.join(''))

        # caption
        # FIXME - if caption isn't set to caption, use "" because the
        # description could be quite long and take a long time to make
        # safe and display.
        task += '"%s (%s %s)"' % (javascript_quote(ticket['description']),
                                  javascript_quote(ticket['status']),
                                  javascript_quote(ticket['type']))
        task += ');\n'
        task += self.GanttID+'.AddTaskItem(t);\n'
        return task

    def _filter_tickets(self, options, tickets):
        # Build the list of display filters from the configured value
        if not options.get('display') or options['display'] == '':
            displayFilter = {}
        else:
            # The general form is
            # 'display=field:value|field:value...'. Split on pipe to
            # get each part
            displayList = options['display'].split('|')

            # Process each part into the display filter
            displayFilter = {}
            field = None
            for f in displayList:
                parts = f.split(':')
                # Just one part, it's a value for the previous field
                if len(parts) == 1:
                    if field == None:
                        raise TracError(('display option error in "%s".' +
                                         ' Should be "display=f1:v1|f2:v2"' +
                                         ' or "display=f:v1|v2".') %
                                        options['display'])
                    else:
                        value = parts[0]
                else:
                    field = parts[0]
                    value = parts[1]

                if field in displayFilter:
                    displayFilter[field].append(value)
                else:
                    displayFilter[field] = [ value ]

        # If present and 1, true, otherwise false.
        if options.get('omitMilestones') \
                and int(options['omitMilestones']) == 1:
            omitMilestones = True
        else:
            omitMilestones = False

        # Filter the tickets
        filteredTickets = []
        for ticket in tickets:
            # Default to showing every ticket
            fieldDisplay = True

            if omitMilestones and \
                    self.pm.isTracMilestone(ticket):
                fieldDisplay = False
            else:
                # Process each element and disable display if all
                # filters fail to match. ((or) disjunction)
                for f in displayFilter:
                    display = True
                    for v in displayFilter[f]:
                        if ticket[f] == v:
                            display = True
                            break
                        display = False
                    fieldDisplay = fieldDisplay & display

            if fieldDisplay:
                filteredTickets.append(ticket)


        return filteredTickets

    # Sort tickets by options['order'].  For example,
    # order=milestone|wbs sorts by wbs within milestone.
    #
    # http://wiki.python.org/moin/HowTo/Sorting (at
    # #Sort_Stability_and_Complex_Sorts) notes that Python list
    # sorting is stable so you can sort by increasing priority of keys
    # (tertiary, then secondary, then primary) to get a multi-key
    # sort.
    #
    # FIXME - this sorts enums by text, not value.
    def _sortTickets(self, tickets, options):
        # Force milestones to the end
        def msSorter(t1, t2):
            # If t1 is a not milestone and t2 is, t1 comes first
            if not self.pm.isMilestone(t1) and self.pm.isMilestone(t2):
                result = -1
            elif self.pm.isMilestone(t1) and not self.pm.isMilestone(t2):
                result = 1
            else:
                result = 0
            return result

        # Get all the sort fields
        sortFields = options['order'].split('|')

        # If sorting by milestone, force milestone type tickets to the
        # end before any other sort.  The stability of the other sorts
        # will keep them at the end of the milestone group (unless
        # overridden by other fields listed in `order`).
        if 'milestone' in sortFields:
            tickets.sort(msSorter)

        # Reverse sort fields so lowest priority is first
        sortFields.reverse()

        # Do the sort by each field
        for field in sortFields:
            tickets.sort(key=itemgetter(field))

        return tickets


    def _add_tasks(self, options):
        if options.get('sample') and int(options['sample']) != 0:
            tasks = self._add_sample_tasks()
        else:
            tasks = ''
            self.tickets = self._query_tickets(options)

            # Faster lookups for WBS and scheduling.
            self.ticketsByID = {}
            for t in self.tickets:
                self.ticketsByID[t['id']] = t

            # Schedule the tasks
            self.pm.computeSchedule(options, self.tickets)

            # Sort tickets by date for computing WBS
            self.tickets.sort(self._compare_tickets)

            # Compute the WBS
            self._compute_wbs()

            # Set the link for clicking through the Gantt chart
            for t in self.tickets:
                if t['id'] > 0:
                    t['link'] = self.req.href.ticket(t['id'])
                else:
                    t['link'] = self.req.href.milestone(t['summary'])

            # Filter tickets based on options (omitMilestones, display, etc.)
            displayTickets = self._filter_tickets(options, self.tickets)

            # Sort the remaining tickets for display (based on order option).
            displayTickets = self._sortTickets(displayTickets, options)

            for ticket in displayTickets:
                tasks += self._format_ticket(ticket, options)

        return tasks

    def _parse_options(self, content):
        _, options = parse_args(content, strict=False)

        for opt in self.options.keys():
            if opt in options:
                # FIXME - test for success, log on failure
                if isinstance(self.options[opt], (int, long)):
                    options[opt] = int(options[opt])
            else:
                options[opt] = self.options[opt]

        # FIXME - test for success, log on failure
        options['hoursPerDay'] = float(options['hoursPerDay'])

        # Make sure we get all the tickets.  (For complex Gantts,
        # there can be a lot of tickets, easily more than the default
        # max.)
        if 'max' not in options:
            options['max'] = 999

        return options

    def expand_macro(self, formatter, name, content):
        self.req = formatter.req

        # Each invocation needs to build its own map.
        self.classMap = None

        options = self._parse_options(content)

        # Surely we can't create two charts in one microsecond.
        self.GanttID = 'g_'+str(to_utimestamp(datetime.now(localtz)))
        chart = ''
        tasks = self._add_tasks(options)
        if len(tasks) == 0:
            chart += 'No tasks selected.'
        else:
            chart += self._begin_gantt(options)
            chart += self._gantt_options(options)
            chart += tasks
            chart += self._end_gantt(options)

        return chart
