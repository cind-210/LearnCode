// ========== 预设调色板 ==========
const PALETTE = [
    '#ff6b6b', '#4ecdc4', '#ffe66d', '#6c5ce7', '#a29bfe',
    '#fd79a8', '#00b894', '#fdcb6e', '#e056a0', '#686de0',
    '#ff9f43', '#00d2d3', '#f368e0', '#2ed573', '#ff6348',
    '#7bed9f', '#70a1ff', '#ffa502', '#1e90ff', '#ff4757'
];

let colorIndex = 0;
let functionIdCounter = 0;

// ========== 状态 ==========
const functions = [];  // { id, expr, color, latex }

// ========== DOM 元素 ==========
const functionList = document.getElementById('functionList');
const btnAdd = document.getElementById('btnAdd');
const canvas = document.getElementById('graphCanvas');
const ctx = canvas.getContext('2d');

const inputXMin = document.getElementById('xMin');
const inputXMax = document.getElementById('xMax');
const inputYMin = document.getElementById('yMin');
const inputYMax = document.getElementById('yMax');
const btnUpdateView = document.getElementById('btnUpdateView');

// ========== 工具函数 ==========

/** 从方程字符串中提取右侧表达式（去掉 y= 前缀） */
function parseEquation(raw) {
    var s = raw.trim();
    // 匹配 y = 或 y= 开头（支持空格变体）
    var match = s.match(/^y\s*=\s*/i);
    if (match) {
        return s.slice(match[0].length).trim();
    }
    // 如果没有 y= 前缀，返回原字符串（兼容旧输入）
    return s;
}

/** 将用户原始输入标准化为带 y= 前缀的字符串 */
function normalizeInput(raw) {
    var s = raw.trim();
    if (/^y\s*=\s*/i.test(s)) return s;
    return 'y = ' + s;
}

/** 分配下一个颜色 */
function nextColor() {
    const c = PALETTE[colorIndex % PALETTE.length];
    colorIndex++;
    return c;
}

// ---- LaTeX 转换 ----

/** 将 abs(...) 转换为 |...| */
function convertAbs(s) {
    var result = '';
    var i = 0;
    while (i < s.length) {
        if (s.slice(i, i + 4) === 'abs(' && (i === 0 || !/[a-zA-Z]/.test(s[i-1]))) {
            var depth = 1;
            var j = i + 4;
            while (j < s.length && depth > 0) {
                if (s[j] === '(') depth++;
                else if (s[j] === ')') depth--;
                j++;
            }
            if (depth === 0) {
                var inner = s.slice(i + 4, j - 1);
                result += '|' + inner + '|';
                i = j;
                continue;
            }
        }
        result += s[i];
        i++;
    }
    return result;
}

/** 将 math.js 语法中的函数调用的 ) 替换为 } */
function fixParentheses(s) {
    var result = [];
    var i = 0;
    var braceStack = []; // { type: 'cmd', depth: number }

    while (i < s.length) {
        if (s[i] === '\\') {
            var start = i;
            i++;
            while (i < s.length && /[a-zA-Z]/.test(s[i])) i++;
            var cmdName = s.slice(start, i);

            if (i < s.length && s[i] === '{') {
                braceStack.push({ type: 'cmd', depth: 0 });
                result.push(cmdName);
                result.push('{');
                i++;
                continue;
            } else {
                result.push(cmdName);
                continue;
            }
        } else if (s[i] === '(') {
            if (braceStack.length > 0 && braceStack[braceStack.length - 1].type === 'cmd') {
                braceStack[braceStack.length - 1].depth++;
                result.push('(');
            } else {
                result.push('(');
            }
            i++;
        } else if (s[i] === ')') {
            if (braceStack.length > 0 && braceStack[braceStack.length - 1].type === 'cmd') {
                var top = braceStack[braceStack.length - 1];
                if (top.depth === 0) {
                    braceStack.pop();
                    result.push('}');
                } else {
                    top.depth--;
                    result.push(')');
                }
            } else {
                result.push(')');
            }
            i++;
        } else {
            result.push(s[i]);
            i++;
        }
    }

    return result.join('');
}

/** 将 math.js 表达式转换为 LaTeX 字符串 */
function exprToLatex(expr) {
    var latex = expr.trim();

    // Step 1: 先处理 abs() → |...|
    latex = convertAbs(latex);

    // Step 2: 替换函数名
    var funcMap = [
        ['asin', '\\arcsin'], ['acos', '\\arccos'], ['atan', '\\arctan'],
        ['sinh', '\\sinh'], ['cosh', '\\cosh'], ['tanh', '\\tanh'],
        ['sin', '\\sin'], ['cos', '\\cos'], ['tan', '\\tan'],
        ['log', '\\log'], ['ln', '\\ln'],
        ['sqrt', '\\sqrt'],
        ['cbrt', '\\sqrt[3]'],
    ];

    for (var k = 0; k < funcMap.length; k++) {
        var fn = funcMap[k][0];
        var tex = funcMap[k][1];
        var regex = new RegExp('\\b' + fn + '\\(', 'g');
        latex = latex.replace(regex, tex + '{');
    }

    // Step 3: 将函数的 ) 闭合为 }
    latex = fixParentheses(latex);

    // Step 4: 替换乘号
    latex = latex.replace(/\*/g, ' \\cdot ');

    // Step 5: 替换常量
    latex = latex.replace(/\bpi\b/g, '\\pi');

    return latex;
}

// ---- 表达式求值 ----

/** 评估用户输入在给定 x 处的值（自动解析 y= 前缀） */
function evaluateInput(raw, xVal) {
    var expr = parseEquation(raw);
    try {
        var node = math.parse(expr);
        var compiled = node.compile();
        var scope = { x: xVal, pi: Math.PI, e: Math.E };
        var result = compiled.evaluate(scope);
        if (typeof result === 'number' && isFinite(result)) {
            return result;
        }
        return NaN;
    } catch (e) {
        return NaN;
    }
}

/** 验证用户输入（解析 y= 后验证右侧表达式） */
function validateInput(raw) {
    var expr = parseEquation(raw);
    if (!expr) return false;  // 空表达式无效
    try {
        var node = math.parse(expr);
        node.compile();
        return true;
    } catch (e) {
        return false;
    }
}

// ========== 绘图 ==========

function calcTickDist(range) {
    var rough = range / 8;
    var magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    var residual = rough / magnitude;
    var nice;
    if (residual <= 1.5) nice = 1;
    else if (residual <= 3) nice = 2;
    else if (residual <= 7) nice = 5;
    else nice = 10;
    return nice * magnitude;
}

function formatNum(n) {
    if (Number.isInteger(n)) return n.toString();
    var s = n.toFixed(6);
    return parseFloat(s).toString();
}

function draw() {
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    var w = rect.width;
    var h = rect.height;

    // 尺寸为 0 时跳过（布局尚未完成）
    if (w <= 0 || h <= 0) return;

    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    // 背景
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, w, h);

    var xMin = parseFloat(inputXMin.value) || -10;
    var xMax = parseFloat(inputXMax.value) || 10;
    var yMin = parseFloat(inputYMin.value) || -10;
    var yMax = parseFloat(inputYMax.value) || 10;

    var xRange = xMax - xMin;
    var yRange = yMax - yMin;

    var margin = { top: 30, right: 30, bottom: 40, left: 50 };
    var plotW = w - margin.left - margin.right;
    var plotH = h - margin.top - margin.bottom;

    function pxX(x) { return margin.left + (x - xMin) / xRange * plotW; }
    function pxY(y) { return margin.top + (yMax - y) / yRange * plotH; }

    // 裁剪区域
    ctx.save();
    ctx.beginPath();
    ctx.rect(margin.left, margin.top, plotW, plotH);
    ctx.clip();

    // 网格
    drawGrid(xMin, xMax, yMin, yMax, pxX, pxY, margin, plotW, plotH);

    // 函数曲线
    for (var fi = 0; fi < functions.length; fi++) {
        var fn = functions[fi];
        if (!fn.expr.trim()) continue;
        drawCurve(fn.expr, fn.color, xMin, xMax, yMin, yMax, pxX, pxY, margin, plotW, plotH);
    }

    ctx.restore();

    // 坐标轴
    drawAxes(xMin, xMax, yMin, yMax, pxX, pxY, margin, plotW, plotH, w, h);
}

function drawGrid(xMin, xMax, yMin, yMax, pxX, pxY, margin, plotW, plotH) {
    ctx.strokeStyle = '#1a2a3a';
    ctx.lineWidth = 1;

    var xTickDist = calcTickDist(xMax - xMin);
    var yTickDist = calcTickDist(yMax - yMin);

    var xStart = Math.ceil(xMin / xTickDist) * xTickDist;
    for (var x = xStart; x <= xMax; x += xTickDist) {
        var sx = pxX(x);
        ctx.beginPath();
        ctx.moveTo(sx, margin.top);
        ctx.lineTo(sx, margin.top + plotH);
        ctx.stroke();
    }

    var yStart = Math.ceil(yMin / yTickDist) * yTickDist;
    for (var y = yStart; y <= yMax; y += yTickDist) {
        var sy = pxY(y);
        ctx.beginPath();
        ctx.moveTo(margin.left, sy);
        ctx.lineTo(margin.left + plotW, sy);
        ctx.stroke();
    }
}

function drawAxes(xMin, xMax, yMin, yMax, pxX, pxY, margin, plotW, plotH, w, h) {
    ctx.strokeStyle = '#556';
    ctx.lineWidth = 1.5;
    ctx.fillStyle = '#aaa';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'center';

    // X 轴
    if (yMin <= 0 && yMax >= 0) {
        var sy = pxY(0);
        ctx.beginPath();
        ctx.moveTo(margin.left, sy);
        ctx.lineTo(margin.left + plotW, sy);
        ctx.stroke();

        var xTickDist = calcTickDist(xMax - xMin);
        var xStart = Math.ceil(xMin / xTickDist) * xTickDist;
        for (var x = xStart; x <= xMax; x += xTickDist) {
            if (Math.abs(x) < 0.0001) continue;
            var sx = pxX(x);
            ctx.beginPath();
            ctx.moveTo(sx, sy - 5);
            ctx.lineTo(sx, sy + 5);
            ctx.stroke();
            ctx.fillText(formatNum(x), sx, sy + 18);
        }
    }

    // Y 轴
    if (xMin <= 0 && xMax >= 0) {
        var sx = pxX(0);
        ctx.beginPath();
        ctx.moveTo(sx, margin.top);
        ctx.lineTo(sx, margin.top + plotH);
        ctx.stroke();

        var yTickDist = calcTickDist(yMax - yMin);
        var yStart = Math.ceil(yMin / yTickDist) * yTickDist;
        ctx.textAlign = 'right';
        for (var y = yStart; y <= yMax; y += yTickDist) {
            if (Math.abs(y) < 0.0001) continue;
            var sy = pxY(y);
            ctx.beginPath();
            ctx.moveTo(sx - 5, sy);
            ctx.lineTo(sx + 5, sy);
            ctx.stroke();
            ctx.fillText(formatNum(y), sx - 10, sy + 4);
        }
    }

    // 原点标签
    if (xMin <= 0 && xMax >= 0 && yMin <= 0 && yMax >= 0) {
        ctx.textAlign = 'right';
        ctx.fillText('O', pxX(0) - 10, pxY(0) - 6);
    }

    // 轴标签
    ctx.fillStyle = '#ccc';
    ctx.font = 'bold 13px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('x', margin.left + plotW + 15, pxY(0) + 5);
    ctx.fillText('y', pxX(0), margin.top - 10);
}

function drawCurve(expr, color, xMin, xMax, yMin, yMax, pxX, pxY, margin, plotW, plotH) {
    var steps = Math.floor(plotW * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();

    var started = false;
    var prevY = NaN;
    var dx = (xMax - xMin) / steps;
    var yRange = yMax - yMin;
    var jumpThreshold = yRange / 2; // 超过一半范围视为不连续

    for (var i = 0; i <= steps; i++) {
        var x = xMin + i * dx;
        var y = evaluateInput(expr, x);

        if (isNaN(y) || !isFinite(y)) {
            started = false;
            prevY = NaN;
            continue;
        }

        var sx = pxX(x);
        var sy = pxY(y);

        // 检测不连续
        if (started && Math.abs(y - prevY) > jumpThreshold) {
            ctx.stroke();
            ctx.beginPath();
            started = false;
        }

        if (!started) {
            ctx.moveTo(sx, sy);
            started = true;
        } else {
            ctx.lineTo(sx, sy);
        }
        prevY = y;
    }

    ctx.stroke();
}

// ========== 函数条目管理 ==========

function escapeHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

function renderDisplay(display, rawInput) {
    var expr = parseEquation(rawInput);  // 提取右侧表达式用于 LaTeX
    var latex = exprToLatex(expr);
    try {
        katex.render('y = ' + latex, display, {
            throwOnError: false,
            fontSize: '1.1rem',
        });
    } catch (e) {
        display.textContent = rawInput;
    }
}

function createFunctionItem(id, expr, color) {
    var item = document.createElement('div');
    item.className = 'fn-item';
    item.dataset.id = id;

    // 头部
    var header = document.createElement('div');
    header.className = 'fn-header';

    var indexSpan = document.createElement('span');
    indexSpan.className = 'fn-index';

    var display = document.createElement('div');
    display.className = 'fn-display';

    var input = document.createElement('input');
    input.className = 'fn-input';
    input.type = 'text';
    input.placeholder = '例: y = x^2 + sin(x)';
    input.style.display = 'none';

    // 颜色
    var colorWrap = document.createElement('div');
    colorWrap.className = 'fn-color-wrap';
    var colorDot = document.createElement('div');
    colorDot.className = 'fn-color-dot';
    colorDot.style.backgroundColor = color;
    var colorInput = document.createElement('input');
    colorInput.className = 'fn-color-input';
    colorInput.type = 'color';
    colorInput.value = color;
    colorWrap.appendChild(colorDot);
    colorWrap.appendChild(colorInput);

    header.appendChild(indexSpan);
    header.appendChild(display);
    header.appendChild(input);
    header.appendChild(colorWrap);

    // 错误提示
    var errorDiv = document.createElement('div');
    errorDiv.className = 'fn-error';
    errorDiv.style.display = 'none';

    // 操作按钮
    var actions = document.createElement('div');
    actions.className = 'fn-actions';
    var btnDelete = document.createElement('button');
    btnDelete.className = 'fn-btn-delete';
    btnDelete.textContent = '删除';
    actions.appendChild(btnDelete);

    item.appendChild(header);
    item.appendChild(errorDiv);
    item.appendChild(actions);

    // ---- 事件 ----

    display.addEventListener('click', function () {
        enterEditMode(item);
    });

    input.addEventListener('blur', function () {
        exitEditMode(item);
    });

    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            input.blur();
        }
    });

    colorInput.addEventListener('input', function () {
        var newColor = colorInput.value;
        colorDot.style.backgroundColor = newColor;
        var fn = functions.find(function (f) { return f.id === id; });
        if (fn) {
            fn.color = newColor;
            draw();
        }
    });

    btnDelete.addEventListener('click', function () {
        var idx = functions.findIndex(function (f) { return f.id === id; });
        if (idx !== -1) {
            functions.splice(idx, 1);
        }
        item.remove();
        updateIndices();
        draw();
    });

    // 初始化显示
    if (expr.trim()) {
        renderDisplay(display, expr);
    } else {
        display.innerHTML = '<span class="placeholder">点击输入方程...</span>';
    }

    functionList.appendChild(item);
    return item;
}

function enterEditMode(item) {
    var display = item.querySelector('.fn-display');
    var input = item.querySelector('.fn-input');
    var id = parseInt(item.dataset.id);
    var fn = functions.find(function (f) { return f.id === id; });

    display.style.display = 'none';
    input.style.display = 'block';
    input.value = fn ? fn.expr : '';
    input.focus();
    input.select();
}

function exitEditMode(item) {
    var display = item.querySelector('.fn-display');
    var input = item.querySelector('.fn-input');
    var errorDiv = item.querySelector('.fn-error');
    var id = parseInt(item.dataset.id);
    var fn = functions.find(function (f) { return f.id === id; });

    var raw = input.value.trim();

    // 存储标准化后的输入（带 y=）
    if (fn) {
        fn.expr = raw;
    }

    input.style.display = 'none';
    display.style.display = 'flex';
    errorDiv.style.display = 'none';

    if (raw) {
        var valid = validateInput(raw);
        if (valid) {
            renderDisplay(display, raw);
        } else {
            display.innerHTML = '<span style="color:#ff6b7a;">' + escapeHtml(raw) + '</span>';
            errorDiv.textContent = '表达式无效，请检查语法（示例：y = x^2 + sin(x)）';
            errorDiv.style.display = 'block';
        }
    } else {
        display.innerHTML = '<span class="placeholder">点击输入方程...</span>';
    }

    draw();
}

function updateIndices() {
    var items = functionList.querySelectorAll('.fn-item');
    items.forEach(function (item, i) {
        var span = item.querySelector('.fn-index');
        span.textContent = 'f' + (i + 1);
    });
}

function addFunction(expr, color) {
    if (!expr) expr = '';
    if (!color) color = nextColor();
    var id = ++functionIdCounter;
    functions.push({ id: id, expr: expr, color: color, latex: '' });
    createFunctionItem(id, expr, color);
    updateIndices();
    draw();
}

// ========== 事件绑定 ==========

btnAdd.addEventListener('click', function () { addFunction(); });

btnUpdateView.addEventListener('click', function () { draw(); });

window.addEventListener('resize', function () { draw(); });

// ========== 鼠标交互：缩放与平移 ==========

var dragInfo = null; // { startX, startY, xMin0, xMax0, yMin0, yMax0 }

/** 像素坐标 → 图坐标 */
function pixelToCoord(px, py) {
    var rect = canvas.getBoundingClientRect();
    var w = rect.width;
    var h = rect.height;

    var xMin = parseFloat(inputXMin.value) || -10;
    var xMax = parseFloat(inputXMax.value) || 10;
    var yMin = parseFloat(inputYMin.value) || -10;
    var yMax = parseFloat(inputYMax.value) || 10;

    var margin = { top: 30, right: 30, bottom: 40, left: 50 };
    var plotW = w - margin.left - margin.right;
    var plotH = h - margin.top - margin.bottom;

    var xRange = xMax - xMin;
    var yRange = yMax - yMin;

    var cx = xMin + (px - margin.left) / plotW * xRange;
    var cy = yMax - (py - margin.top) / plotH * yRange;
    return { x: cx, y: cy };
}

/** 滚轮缩放：以鼠标位置为中心 */
canvas.addEventListener('wheel', function (e) {
    e.preventDefault();

    var coord = pixelToCoord(e.offsetX, e.offsetY);
    var cx = coord.x;
    var cy = coord.y;

    var xMin = parseFloat(inputXMin.value) || -10;
    var xMax = parseFloat(inputXMax.value) || 10;
    var yMin = parseFloat(inputYMin.value) || -10;
    var yMax = parseFloat(inputYMax.value) || 10;

    // 缩放因子：向下滚放大，向上滚缩小
    var factor = (e.deltaY < 0) ? 0.85 : 1.15;

    var newXMin = cx - (cx - xMin) * factor;
    var newXMax = cx + (xMax - cx) * factor;
    var newYMin = cy - (cy - yMin) * factor;
    var newYMax = cy + (yMax - cy) * factor;

    // 限制最小范围防止过度缩放
    if (newXMax - newXMin < 0.01 || newYMax - newYMin < 0.01) return;

    inputXMin.value = +newXMin.toFixed(4);
    inputXMax.value = +newXMax.toFixed(4);
    inputYMin.value = +newYMin.toFixed(4);
    inputYMax.value = +newYMax.toFixed(4);

    draw();
}, { passive: false });

/** 鼠标按下开始平移 */
canvas.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return; // 只响应左键
    dragInfo = {
        startX: e.offsetX,
        startY: e.offsetY,
        xMin0: parseFloat(inputXMin.value) || -10,
        xMax0: parseFloat(inputXMax.value) || 10,
        yMin0: parseFloat(inputYMin.value) || -10,
        yMax0: parseFloat(inputYMax.value) || 10,
    };
    canvas.style.cursor = 'grabbing';
    e.preventDefault();
});

/** 鼠标移动平移 */
window.addEventListener('mousemove', function (e) {
    if (!dragInfo) return;

    var rect = canvas.getBoundingClientRect();
    var w = rect.width;
    var h = rect.height;

    var margin = { top: 30, right: 30, bottom: 40, left: 50 };
    var plotW = w - margin.left - margin.right;
    var plotH = h - margin.top - margin.bottom;

    var xRange = dragInfo.xMax0 - dragInfo.xMin0;
    var yRange = dragInfo.yMax0 - dragInfo.yMin0;

    var dx = (e.clientX - rect.left) - dragInfo.startX;
    var dy = (e.clientY - rect.top) - dragInfo.startY;

    // 像素位移 → 图坐标位移
    var dxGraph = -dx / plotW * xRange;
    var dyGraph =  dy / plotH * yRange;

    inputXMin.value = +(dragInfo.xMin0 + dxGraph).toFixed(4);
    inputXMax.value = +(dragInfo.xMax0 + dxGraph).toFixed(4);
    inputYMin.value = +(dragInfo.yMin0 + dyGraph).toFixed(4);
    inputYMax.value = +(dragInfo.yMax0 + dyGraph).toFixed(4);

    draw();
});

/** 鼠标释放停止平移 */
window.addEventListener('mouseup', function (e) {
    if (!dragInfo) return;
    dragInfo = null;
    canvas.style.cursor = 'default';
});

// ========== 初始化 ==========

function init() {
    addFunction('y = x^2', PALETTE[0]);
    addFunction('y = sin(x)', PALETTE[1]);
}

// 短暂延迟确保 canvas 尺寸计算正确后初始化
setTimeout(init, 50);