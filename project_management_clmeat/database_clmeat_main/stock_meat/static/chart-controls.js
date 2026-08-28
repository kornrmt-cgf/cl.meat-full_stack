/**
 * CLChart - Normalized SVG timeseries chart component for CL.MEAT
 * Usage:
 *   new CLChart('containerId', {
 *     data: [{_date:'2026-08-23', sales:100, profit:20}, ...],
 *     series: [{key:'sales', label:'ยอดขาย', color:'#38bdf8'}, ...],
 *     labelKey: '_date',   // optional, defaults to _date
 *     scale: 'day',        // 'hour' | 'day' | 'month'
 *     preset: '30',        // '7' | '30' | '90' | 'all'
 *     height: 180
 *   });
 */
function CLChart(id, opts) {
    this.id = id;
    this.el = document.getElementById(id);
    this.data = opts.data || [];
    this.series = opts.series || [];
    this.labelKey = opts.labelKey || '_date';
    this.scale = opts.scale || 'day';
    this._preset = opts.preset || '30';
    this.height = opts.height || 180;
    this._useGapFill = opts.gapFill !== false;
    CLChart._inst[id] = this;
    this.render();
}
CLChart._inst = {};

/* ── Controls ─────────────────────────────────── */
CLChart.prototype._controls = function () {
    var self = this;
    var scales = [['hour','ชั่วโมง'],['day','วัน'],['month','เดือน']];
    var presets = [['7','7 วัน'],['30','30 วัน'],['90','90 วัน'],['all','ทั้งหมด']];
    var mkBtns = function (arr, cur, fn) {
        return arr.map(function (p) {
            var c = p[0] === cur ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
            return '<button class="' + c + '" onclick="CLChart._inst[\'' + self.id + '\'].' + fn + '(\'' + p[0] + '\')">' + p[1] + '</button>';
        }).join('');
    };
    return '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:10px">' +
        '<div style="display:flex;gap:3px">' + mkBtns(scales, this.scale, '_setScale') + '</div>' +
        '<div style="display:flex;gap:3px">' +
        '<button class="' + (this.type !== 'line' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm') + '" onclick="CLChart._inst[\'' + self.id + '\']._setType(\'bar\')">📊 แท่ง</button>' +
        '<button class="' + (this.type === 'line' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm') + '" onclick="CLChart._inst[\'' + self.id + '\']._setType(\'line\')">📈 เส้น</button>' +
        '</div>' +
        '<div style="display:flex;gap:3px">' + mkBtns(presets, this._preset, '_setPreset') + '</div>' +
        '</div>';
};

/* ── Helpers ──────────────────────────────────── */
CLChart.prototype._parseDate = function (s) {
    if (!s) return null;
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
};

CLChart.prototype._groupKey = function (d) {
    var dt = this._parseDate(d._date);
    if (!dt) return null;
    if (this.scale === 'hour') return dt.toISOString().slice(0, 13);
    if (this.scale === 'month') return dt.toISOString().slice(0, 7);
    return dt.toISOString().slice(0, 10);
};

CLChart.prototype._groupLabel = function (key) {
    var parts;
    if (this.scale === 'hour') {
        parts = key.split('T');
        var day = parts[0].split('-')[2];
        var mo = parts[0].split('-')[1];
        var hr = parts[1] || '00';
        return day + '/' + mo + ' ' + hr;
    }
    if (this.scale === 'month') {
        parts = key.split('-');
        return parseInt(parts[1]) + '/' + parts[0];
    }
    // day
    parts = key.split('-');
    return parseInt(parts[2]) + '/' + parseInt(parts[1]);
};

CLChart.prototype._filterData = function () {
    var p = this._preset;
    if (p === 'all') return this.data.slice();
    var days = parseInt(p) || 30;
    var since = new Date();
    since.setDate(since.getDate() - days);
    since.setHours(0, 0, 0, 0);
    return this.data.filter(function (d) {
        var dt = new Date(d._date);
        return !isNaN(dt.getTime()) && dt >= since;
    });
};

CLChart.prototype._gapFill = function (grouped) {
    if (!this._useGapFill || grouped.length < 2) return grouped;
    var keys = Object.keys(grouped).sort();
    var start = new Date(keys[0].length >= 10 ? keys[0] : keys[0] + '-01');
    var end = new Date(keys[keys.length - 1].length >= 10 ? keys[keys.length - 1] : keys[keys.length - 1] + '-28');
    var step = this.scale === 'hour' ? 36e5 : this.scale === 'day' ? 864e5 : 2592e6;
    var cur = start.getTime();
    var result = [];
    var self = this;
    while (cur <= end.getTime()) {
        var d = new Date(cur);
        var k;
        if (self.scale === 'hour') k = d.toISOString().slice(0, 13);
        else if (self.scale === 'month') k = d.toISOString().slice(0, 7);
        else k = d.toISOString().slice(0, 10);
        if (grouped[k]) {
            result.push(grouped[k]);
        } else {
            var empty = { _key: k, label: self._groupLabel(k), _date: d.toISOString() };
            self.series.forEach(function (s) { empty[s.key] = 0; });
            result.push(empty);
        }
        cur += step;
    }
    return result;
};

CLChart.prototype._getGrouped = function () {
    var filtered = this._filterData();
    var self = this;
    var groups = {};
    filtered.forEach(function (d) {
        var k = self._groupKey(d);
        if (!k) return;
        if (!groups[k]) {
            groups[k] = { _key: k, label: self._groupLabel(k), _date: d._date };
            self.series.forEach(function (s) { groups[k][s.key] = 0; });
        }
        self.series.forEach(function (s) {
            groups[k][s.key] += parseFloat(d[s.key]) || 0;
        });
    });
    return self._gapFill(groups);
};

/* ── SVG Renderers ────────────────────────────── */
CLChart.prototype.render = function () {
    if (!this.el) return;
    this.el.style.overflow = 'hidden';
    var grouped = this._getGrouped();
    this.el.innerHTML = this._controls() + '<div id="' + this.id + '_svg" style="overflow:hidden"></div>';
    this._drawSVG(grouped);
};

CLChart.prototype._drawSVG = function (data) {
    var box = document.getElementById(this.id + '_svg');
    if (!box) return;
    if (!data.length) {
        box.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-secondary);font-size:13px">ไม่มีข้อมูลในช่วงที่เลือก</div>';
        return;
    }

    var W = 800, padL = 50, padR = 16, padT = 10, padB = 36;
    var chartH = this.height - padT - padB;
    var chartW = W - padL - padR;

    // Max value for Y axis
    var maxVal = 1;
    var self = this;
    data.forEach(function (d) {
        self.series.forEach(function (s) {
            var v = Math.abs(parseFloat(d[s.key]) || 0);
            if (v > maxVal) maxVal = v;
        });
    });
    maxVal = maxVal * 1.1; // 10% headroom

    // Y axis labels (5 ticks)
    var yTicks = 5;
    var yLabels = '';
    for (var i = 0; i <= yTicks; i++) {
        var yVal = (maxVal / yTicks) * i;
        var yPos = padT + chartH - (i / yTicks) * chartH;
        yLabels += '<line x1="' + padL + '" y1="' + yPos + '" x2="' + (W - padR) + '" y2="' + yPos + '" stroke="var(--border)" stroke-width="0.5" opacity="0.4"/>';
        yLabels += '<text x="' + (padL - 6) + '" y="' + (yPos + 3) + '" text-anchor="end" font-size="9" fill="var(--text-secondary)">' + self._fmtShort(yVal) + '</text>';
    }

    // X axis + data
    var n = data.length;
    var barW = Math.min(20, Math.max(6, (chartW / n) * 0.6));
    var barGap = chartW / n;

    var bars = '';
    var xLabels = '';
    var linePaths = {};
    this.series.forEach(function (s) { linePaths[s.key] = []; });

    data.forEach(function (d, i) {
        var cx = padL + i * barGap + barGap / 2;

        // X label
        var showLabel = n <= 30 || i % Math.ceil(n / 20) === 0;
        if (showLabel) {
            xLabels += '<text x="' + cx + '" y="' + (padT + chartH + 16) + '" text-anchor="middle" font-size="9" fill="var(--text-secondary)"' +
                (n > 15 ? ' transform="rotate(-35,' + cx + ',' + (padT + chartH + 16) + ')"' : '') + '>' +
                (d.label || '') + '</text>';
        }

        self.series.forEach(function (s, si) {
            var val = parseFloat(d[s.key]) || 0;
            var barH = Math.max(1, (Math.abs(val) / maxVal) * chartH);
            var y = padT + chartH - barH;
            var x = cx - barW / 2 + si * (barW / self.series.length + 1);
            var w = Math.max(4, barW / self.series.length - 1);

            if (self.type === 'line') {
                linePaths[s.key].push({ x: cx, y: y, val: val, label: d.label });
            } else {
                bars += '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + barH + '" rx="2" fill="' + s.color + '" opacity="0.85"' +
                    ' onmouseenter="CLChart_tip(evt,\'' + (d.label || '') + ' | ' + s.label + ': ' + self._fmt(val) + '\')" onmouseleave="CLChart_hideTip()"' +
                    ' style="cursor:pointer"/>';
            }
        });
    });

    // Line paths
    var lines = '';
    if (this.type === 'line') {
        this.series.forEach(function (s) {
            var pts = linePaths[s.key];
            if (pts.length < 2) return;
            var pathD = pts.map(function (p, i) {
                return (i === 0 ? 'M' : 'L') + p.x + ',' + p.y;
            }).join(' ');
            lines += '<path d="' + pathD + '" fill="none" stroke="' + s.color + '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
            pts.forEach(function (p) {
                lines += '<circle cx="' + p.x + '" cy="' + p.y + '" r="3.5" fill="' + s.color + '" stroke="var(--card)" stroke-width="1.5"' +
                    ' onmouseenter="CLChart_tip(evt,\'' + p.label + ' | ' + s.label + ': ' + self._fmt(p.val) + '\')" onmouseleave="CLChart_hideTip()"' +
                    ' style="cursor:pointer"/>';
            });
        });
    }

    // Legend
    var legend = this.series.map(function (s) {
        return '<span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--text-secondary)"><span style="width:10px;height:10px;border-radius:2px;background:' + s.color + ';display:inline-block"></span>' + s.label + '</span>';
    }).join('  ');

    var svg = '<svg viewBox="0 0 ' + W + ' ' + this.height + '" style="width:100%;height:' + this.height + 'px;display:block">' +
        yLabels +
        '<line x1="' + padL + '" y1="' + (padT + chartH) + '" x2="' + (W - padR) + '" y2="' + (padT + chartH) + '" stroke="var(--border)" stroke-width="1"/>' +
        bars + lines + xLabels + '</svg>' +
        '<div style="display:flex;gap:12px;margin-top:4px;padding-left:' + padL + 'px">' + legend + '</div>';

    box.innerHTML = svg;
};

CLChart.prototype._fmtShort = function (v) {
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toFixed(0);
};
CLChart.prototype._fmt = function (v) {
    return '฿' + v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
};

/* ── Setters ──────────────────────────────────── */
CLChart.prototype._setScale = function (s) { this.scale = s; this.render(); };
CLChart.prototype._setPreset = function (p) { this._preset = p; this.render(); };
CLChart.prototype._setType = function (t) { this.type = t; this.render(); };
CLChart.prototype.setData = function (d) { this.data = d; this.render(); };

/* ── Tooltip ──────────────────────────────────── */
var _cltip = null;
function CLChart_tip(e, text) {
    if (!_cltip) {
        _cltip = document.createElement('div');
        _cltip.style.cssText = 'position:fixed;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,.18);z-index:9999;pointer-events:none;white-space:nowrap;transition:opacity .1s';
        document.body.appendChild(_cltip);
    }
    _cltip.textContent = text;
    _cltip.style.opacity = '1';
    _cltip.style.left = (e.clientX + 14) + 'px';
    _cltip.style.top = (e.clientY - 10) + 'px';
}
function CLChart_hideTip() { if (_cltip) _cltip.style.opacity = '0'; }
