/**
 * 通用碳账户管理系统 — 前端应用
 * 纯 Vanilla JS，无框架依赖
 */
(function() {
'use strict';

// ═══════════════════════════════════════════════════════════
// 自定义下拉选择框 CustomSelect
// 替代原生 <select>，提供样式化的下拉体验
// 用法: <select class="custom-select" data-search="true">...</select>
// ═══════════════════════════════════════════════════════════

class CustomSelect {
    constructor(nativeSelect) {
        this.native = nativeSelect;
        this.wrap = null;
        this.dropdown = null;
        this.searchInput = null;
        this._open = false;
        this._build();
        this._bind();
    }

    _build() {
        // 隐藏原生 select
        this.native.style.display = 'none';

        // 创建包装容器
        this.wrap = document.createElement('div');
        this.wrap.className = 'cs-wrap';
        this.native.parentNode.insertBefore(this.wrap, this.native);
        this.wrap.appendChild(this.native);

        // 触发器按钮
        const trigger = document.createElement('div');
        trigger.className = 'cs-trigger';
        trigger.tabIndex = 0;
        trigger.innerHTML = '<span class="cs-selected-text cs-placeholder">' +
            (this.native.getAttribute('data-placeholder') || '请选择') +
            '</span><span class="cs-arrow">▼</span>';
        this.wrap.appendChild(trigger);

        // 下拉面板
        this.dropdown = document.createElement('div');
        this.dropdown.className = 'cs-dropdown';

        // 搜索框（data-search="true" 时启用）
        const enableSearch = this.native.dataset.search === 'true';
        if (enableSearch) {
            const searchWrap = document.createElement('div');
            searchWrap.className = 'cs-search';
            this.searchInput = document.createElement('input');
            this.searchInput.type = 'text';
            this.searchInput.placeholder = '搜索...';
            searchWrap.appendChild(this.searchInput);
            this.dropdown.appendChild(searchWrap);
        }

        // 选项列表
        this._appendOptions();
        this.wrap.appendChild(this.dropdown);
        this._syncTrigger();
    }

    // 把原生 select 的 option 克隆成下拉项。
    // 跳过被隐藏的 option：有些 select（如 #presetType）用 class="hidden"
    // 控制选项显隐，克隆时若不跳过，隐藏项会照常出现在自定义下拉里。
    _appendOptions() {
        this.native.querySelectorAll('option').forEach((opt, idx) => {
            if (opt.hidden || opt.classList.contains('hidden') || opt.disabled) return;
            const div = document.createElement('div');
            div.className = 'cs-option';
            div.textContent = opt.textContent || opt.value;
            div.dataset.value = opt.value;
            div.dataset.searchText = opt.dataset.searchText || opt.textContent || opt.value;
            div.dataset.index = idx;
            if (opt.selected) div.classList.add('selected');
            this.dropdown.appendChild(div);
        });
    }

    _bind() {
        const trigger = this.wrap.querySelector('.cs-trigger');

        // 点击触发器 → 开关下拉
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            this._open ? this.close() : this.open();
        });

        // 键盘支持
        trigger.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this._open ? this.close() : this.open(); }
            if (e.key === 'ArrowDown') { e.preventDefault(); this.open(); this._focusOption(0); }
        });

        // 选项点击
        this.dropdown.addEventListener('click', (e) => {
            const opt = e.target.closest('.cs-option');
            if (!opt) return;
            const val = opt.dataset.value;
            this._select(val, opt);
            this.close();
            // 触发原生 change 事件
            this.native.dispatchEvent(new Event('change', { bubbles: true }));
        });

        // 搜索过滤
        if (this.searchInput) {
            this.searchInput.addEventListener('input', () => this._filter());
            this.searchInput.addEventListener('click', (e) => e.stopPropagation());
            this.searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') { this.close(); trigger.focus(); }
            });
        }

        // 点击外部关闭
        document.addEventListener('click', (e) => {
            if (this._open && !this.wrap.contains(e.target)) this.close();
        });

        // Escape 关闭
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this._open) { this.close(); trigger.focus(); }
        });

        // 监听原生 select 变化（程序化更改）
        this.native.addEventListener('change', () => this._syncTrigger());
    }

    open() {
        if (this._open) return;
        // 关闭其他已打开的 CustomSelect
        document.querySelectorAll('.cs-wrap.open').forEach(w => {
            if (w !== this.wrap) w.classList.remove('open');
        });
        // 重建下拉选项（以反映动态变更的选项）
        this._rebuildOptions();
        this.wrap.classList.add('open');
        this._open = true;
        // 滚动选中项到可见
        const sel = this.dropdown.querySelector('.cs-option.selected');
        if (sel) sel.scrollIntoView({ block: 'nearest' });
        // 聚焦搜索框
        if (this.searchInput) {
            this.searchInput.value = '';
            this._filter();
            setTimeout(() => this.searchInput.focus(), 50);
        }
    }

    _rebuildOptions() {
        // 清空旧选项（保留搜索框和无匹配提示）
        const toRemove = [];
        this.dropdown.childNodes.forEach(c => {
            if (c.classList && (c.classList.contains('cs-option') || c.classList.contains('cs-no-match'))) {
                toRemove.push(c);
            }
        });
        toRemove.forEach(c => c.remove());
        this._appendOptions();
    }

    close() {
        this.wrap.classList.remove('open');
        this._open = false;
    }

    _select(value, optEl) {
        this.native.value = value;
        this.dropdown.querySelectorAll('.cs-option').forEach(o => o.classList.remove('selected'));
        if (optEl) optEl.classList.add('selected');
        this._syncTrigger();
    }

    _syncTrigger() {
        const trigger = this.wrap.querySelector('.cs-trigger');
        const textEl = trigger.querySelector('.cs-selected-text');
        const val = this.native.value;
        const opt = this.native.querySelector('option[value="' + val.replace(/"/g, '\\"') + '"]');
        if (opt) {
            textEl.textContent = opt.textContent;
            textEl.className = 'cs-selected-text';
        } else {
            textEl.textContent = this.native.getAttribute('data-placeholder') || '请选择';
            textEl.className = 'cs-selected-text cs-placeholder';
        }
    }

    _filter() {
        const q = (this.searchInput ? this.searchInput.value : '').toLowerCase();
        const options = this.dropdown.querySelectorAll('.cs-option');
        let hasMatch = false;
        options.forEach(opt => {
            const match = !q || opt.dataset.searchText.toLowerCase().includes(q);
            opt.classList.toggle('filtered-out', !match);
            if (match) hasMatch = true;
        });
        // 无匹配提示
        let noMatch = this.dropdown.querySelector('.cs-no-match');
        if (!hasMatch) {
            if (!noMatch) {
                noMatch = document.createElement('div');
                noMatch.className = 'cs-no-match';
                noMatch.textContent = '无匹配选项';
                this.dropdown.appendChild(noMatch);
            }
        } else if (noMatch) {
            noMatch.remove();
        }
    }

    _focusOption(idx) {
        const visible = Array.from(this.dropdown.querySelectorAll('.cs-option')).filter(o => !o.classList.contains('filtered-out'));
        if (visible.length > idx) visible[idx].scrollIntoView({ block: 'nearest' });
    }

    // 静态初始化：扫描所有带 custom-select 类的 select
    static initAll(scope) {
        (scope || document).querySelectorAll('select.custom-select').forEach(s => {
            if (!s._customSelect) s._customSelect = new CustomSelect(s);
        });
    }

    static get(nativeSelect) {
        return nativeSelect._customSelect || null;
    }

    static refresh(nativeSelect) {
        const cs = nativeSelect._customSelect;
        if (cs) { cs._syncTrigger(); cs._filter(); }
    }

    static rebuild(nativeSelect) {
        const cs = nativeSelect._customSelect;
        if (cs) { cs._rebuildOptions(); cs._syncTrigger(); }
    }
}

// ═══════════════════════════════════════════════════════════
// API 工具
// ═══════════════════════════════════════════════════════════

const API = {
    async get(path) {
        const resp = await fetch(path);
        return resp.json();
    },
    async post(path, data) {
        const resp = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return resp.json();
    },
    async put(path, data) {
        const resp = await fetch(path, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return resp.json();
    },
    async del(path) {
        const resp = await fetch(path, { method: 'DELETE' });
        return resp.json();
    },
};

// ═══════════════════════════════════════════════════════════
// 顶部模块（Tab）切换
// ═══════════════════════════════════════════════════════════

// 按页签分发数据加载。切换入口统一走 switchTab，新增页签只需在这里加一行。
function runTabLoader(tabName) {
    // 离开监测页时停止自动刷新（切回监测页时 loadMonitorTab 会重启）
    if (state.monitorTimer) { clearInterval(state.monitorTimer); state.monitorTimer = null; }
    if (tabName === 'dashboard') loadDashboard();
    if (tabName === 'projects') loadProjects();
    if (tabName === 'monitor') loadMonitorTab();
    if (tabName === 'reports') loadReportsTab();
    if (tabName === 'vcer') loadVCERTab();
    if (tabName === 'logs') { initLogsTab(); loadLogsTab(); }
    if (tabName === 'settings') loadSettings();
}

// 唯一的页签切换入口。原先 generateReport() 里复制了一份同样的类名翻转逻辑，
// 两份都得维护（且那份漏了定时器清理），统一收口到这里。
function switchTab(tabName) {
    const btn = document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
    const pane = document.getElementById('tab-' + tabName);
    if (!btn || !pane) return;

    // 重复点当前页签：只刷新数据，不重放入场动画
    // （若先 remove 再 add 同一个 class，浏览器不一定重算样式，动画重不重放不可预期）
    if (btn.classList.contains('active')) {
        runTabLoader(tabName);
        return;
    }

    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    pane.classList.add('active');
    moveTabIndicator(btn);
    runTabLoader(tabName);
}

// ── 页签下划线指示条 ──
function moveTabIndicator(btn, animate) {
    const el = document.getElementById('tabIndicator');
    if (!el || !btn) return;
    if (animate === false) el.classList.add('no-anim');
    el.style.width = btn.offsetWidth + 'px';
    el.style.transform = `translateX(${btn.offsetLeft}px)`;
    if (animate === false) {
        // 强制回流后再恢复过渡，避免这次设置被一起动画掉
        void el.offsetWidth;
        el.classList.remove('no-anim');
    }
}

function initTabIndicator() {
    const reposition = () => moveTabIndicator(document.querySelector('.tab-btn.active'), false);
    reposition();
    window.addEventListener('resize', reposition);
    // 字体加载完宽度会变，重新对一次位（避免指示条和文字错开）
    window.addEventListener('load', reposition);
}

// ═══════════════════════════════════════════════════════════
// UI 工具
// ═══════════════════════════════════════════════════════════

const UI = {
    showStatus(elId, type, msg) {
        const el = document.getElementById(elId);
        if (!el) return;
        el.textContent = msg;
        el.className = `status-msg ${type}`;
        setTimeout(() => { el.classList.add('hidden'); }, 5000);
    },

    // Tab 切换
    initTabs() {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });
        initTabIndicator();
    },

    // 新建/编辑项目弹窗（异步：编辑已有项目要顺带拉机器列表）
    async openProjectModal(project) {
        document.getElementById('projEditId').value = project ? project.id : '';
        document.getElementById('modalTitle').textContent = project ? '编辑项目' : '新建项目';
        document.getElementById('projName').value = project ? project.project_name : '';
        document.getElementById('projSummary').value = project ? (project.project_summary || '') : '';
        document.getElementById('projBaseline').value = project ? (project.baseline_date || '') : '';
        document.getElementById('projTenYearTotal').value = project ? (project.pending_validation_10yr || '') : '';
        document.getElementById('projAnnualReduction').value = project ? (project.estimated_annual_reduction || '') : '';
        document.getElementById('projVcerKey').value = project ? (project.vcer_query_key || '') : '';
        document.getElementById('projVcerPwd').value = project ? (project.vcer_query_pwd || '') : '';

        // 公式类型选择
        const hookSel = document.getElementById('projHook');
        hookSel.innerHTML = '<option value="">-- 选择公式 --</option>';
        (state.formulas || []).forEach(h => {
            hookSel.innerHTML += `<option value="${h.name}">${h.label} (${h.name})</option>`;
        });
        if (project) hookSel.value = project.hook_name || '';

        // 动画打开
        const modal = document.getElementById('projectModal');
        modal.classList.remove('hidden', 'closing');
        // 刷新自定义下拉（公式选择器的选项可能已经被更新）
        CustomSelect.refresh(hookSel);

        // 机器列表：新建时项目 id 都还没有，只能给提示；编辑时拉真实列表
        machineState.projectId = project ? project.id : null;
        machineState.list = [];
        machineState.editingId = null;
        if (project && project.id) {
            await loadProjectMachines(project.id);
        } else {
            renderMachineBlock();
        }
    },

    closeProjectModal() {
        const modal = document.getElementById('projectModal');
        if (modal.classList.contains('hidden')) return;
        modal.classList.add('closing');
        modal.querySelector('.modal-card').addEventListener('animationend', function h() {
            this.removeEventListener('animationend', h);
            // 关闭动画还没走完弹窗又被打开（新建项目保存后要立刻重开配机器），
            // 这时不能再补一刀 hidden，否则刚打开的弹窗会被这条过期回调藏掉
            if (!modal.classList.contains('closing')) return;
            modal.classList.add('hidden');
            modal.classList.remove('closing');
        }, { once: true });
    },

    // 手动数据录入弹窗
    async openManualModal(projectId, machineId) {
        document.getElementById('manualProjId').value = projectId;
        document.getElementById('manualFlow').value = '';
        document.getElementById('manualPower').value = '';
        const now = new Date();
        document.getElementById('manualTs').value = now.toISOString().slice(0, 16);
        const modal = document.getElementById('manualDataModal');
        modal.classList.remove('hidden', 'closing');
        // 数据挂在具体机器上：先开弹窗再拉机器，拉失败也不至于连弹窗都打不开
        await fillMachineSel(document.getElementById('manualMachineSel'), projectId, '-- 选择机器 --', true);
        // 从某台机器的「录入」按钮点进来 → 直接选中那台
        if (machineId) {
            const sel = document.getElementById('manualMachineSel');
            sel.value = String(machineId);
            CustomSelect.refresh(sel);
        }
    },

    closeManualModal() {
        const modal = document.getElementById('manualDataModal');
        if (modal.classList.contains('hidden')) return;
        modal.classList.add('closing');
        modal.querySelector('.modal-card').addEventListener('animationend', function h() {
            this.removeEventListener('animationend', h);
            modal.classList.add('hidden');
            modal.classList.remove('closing');
        }, { once: true });
    },

    // 阶段标签
    stageTag(status) {
        const map = {
            '已通过': 'tag-green', '待审核': 'tag-yellow',
            '已驳回': 'tag-red', '待提交': 'tag-gray',
            '未提交': 'tag-gray',
        };
        return `<span class="tag ${map[status] || 'tag-gray'}">${status}</span>`;
    },

    stageDots(project) {
        const names = ['申请','审定','核查','交易','抵消'];
        let html = '<div class="stage-bar-mini">';
        for (let s = 1; s <= 5; s++) {
            const passed = project[`stage${s}_passed`];
            let cls = passed ? 'passed' : '';
            html += `<span class="stage-dot ${cls}" title="阶段${s}: ${names[s-1]} — ${passed ? '已通过' : '未通过'}"></span>`;
        }
        html += '</div>';
        return html;
    },

    // 格式化数字
    fmtNum(n, decimals) {
        if (n === null || n === undefined || isNaN(n)) return '—';
        return Number(n).toFixed(decimals || 2);
    },
};

// ═══════════════════════════════════════════════════════════
// 全局状态
// ═══════════════════════════════════════════════════════════

const state = {
    projects: [],
    formulas: [],
    monitorTimer: null,
    monitorCountdown: 0,
    selectedMonitorProject: null,
    reportHistoryMode: 'all',
    devMode: false,
    // 断档兜底：{项目id: 待兜底条数}（来自 /api/gap_bridge/pending 的 counts）
    gapCounts: {},
    // 确认框当前正在处理的那次断档的预览结果
    gapPreview: null,
};

// ═══════════════════════════════════════════════════════════
// 仪表盘
// ═══════════════════════════════════════════════════════════

async function loadDashboard() {
    const data = await API.get('/api/dashboard');
    if (data.status !== '成功') return;

    const s = data.summary;
    document.getElementById('statProjects').textContent = s.total_projects;
    document.getElementById('statApplied').textContent = s.applied_projects;
    document.getElementById('statTotalAssets').innerHTML = UI.fmtNum(s.total_carbon_assets, 2) + ' <small>tCO₂e</small>';
    document.getElementById('statAvailable').innerHTML = UI.fmtNum(s.total_available_assets, 2) + ' <small>tCO₂e</small>';
    document.getElementById('statTraded').innerHTML = UI.fmtNum(s.total_traded, 2) + ' <small>tCO₂e</small>';
    document.getElementById('statPending').innerHTML = s.pending_status === 'unknown'
        ? '待核对 <small>促进会核查区间尚未确认</small>'
        : UI.fmtNum(s.total_pending_verification, 2) + ' <small>tCO₂e</small>' +
          (s.pending_status === 'stale' ? ' <small>同步过期</small>' : '');

    // 阶段统计
    const sc = data.stage_counts;
    let stageHtml = '<div class="grid-2col">';
    const stageNames = { stage1:'项目申请', stage2:'项目审定', stage3:'项目核查', stage4:'项目交易', stage5:'碳减排量抵消' };
    for (const [key, counts] of Object.entries(sc)) {
        stageHtml += `<div style="margin-bottom:12px">
            <strong>${stageNames[key] || key}</strong><br>
            <span class="tag tag-green">✅ 已通过: ${counts.passed}</span>
            <span class="tag tag-gray">— 未通过: ${counts.not_passed}</span>
        </div>`;
    }
    stageHtml += '</div>';
    document.getElementById('stageStats').innerHTML = stageHtml;

    // 最近活动 — 同时缓存项目列表供其他 Tab 使用
    const projects = data.projects || [];
    state.projects = projects;
    let activityHtml = projects.length ? '' : '<p class="muted">暂无项目</p>';
    projects.slice(0, 5).forEach(p => {
        activityHtml += `<div style="padding:8px 0;border-bottom:1px solid #f0f0f0">
            <strong>${p.project_name}</strong>
            <span style="color:#999;font-size:12px;float:right">${p.updated_at || ''}</span>
            <br><small>编号: ${p.project_code || '未分配'} | 碳资产: ${UI.fmtNum(p.total_carbon_assets, 2)} tCO₂e</small>
        </div>`;
    });
    document.getElementById('recentActivity').innerHTML = activityHtml;
}

// ═══════════════════════════════════════════════════════════
// 项目管理
// ═══════════════════════════════════════════════════════════

async function loadProjects() {
    const data = await API.get('/api/projects');
    state.projects = data.projects || [];
    renderProjectList();          // 先把列表画出来，别等断档角标

    // 待兜底断档数是**附加信息**：要扫监测数据，库大了会慢（本地 9.7 万行约 0.8s）。
    // 所以异步补，拿到再重画一次按钮；拉失败也不显示按钮，绝不因此把项目列表拖挂。
    API.get('/api/gap_bridge/pending').then(gap => {
        state.gapCounts = (gap && gap.status === '成功' && gap.counts) || {};
        renderProjectList();
    }).catch(() => { state.gapCounts = {}; });
}

function renderProjectList() {
    const container = document.getElementById('projectList');
    if (!container) return;
    if (!state.projects.length) {
        container.innerHTML = '<p class="muted">暂无项目，点击 "+ 新建项目" 创建第一个项目</p>';
        return;
    }

    const query = (document.getElementById('projectSearchInput')?.value || '').trim().toLowerCase();
    const projects = state.projects.filter(p =>
        !query || `${p.project_name || ''} ${p.project_code || ''}`.toLowerCase().includes(query));
    if (!projects.length) {
        container.innerHTML = '<p class="muted">没有匹配的项目，请换个名称或编号试试。</p>';
        return;
    }
    let html = '';
    projects.forEach(p => {
        const applied = !!p.project_code;
        // 机器信息：新响应带 machines 数组，老响应只有项目级 data_source_type，按它兜底
        const machines = Array.isArray(p.machines) ? p.machines : null;
        const machineCount = p.machine_count || (machines ? machines.length : 1) || 1;
        let machineTxt = '';
        if (machines && machines.length) {
            // 机器多时卡片会被撑长，只列前 3 台
            machineTxt = machines.slice(0, 3)
                .map(m => `${_esc(m.name)}(${_esc(DS_TYPE_LABELS[m.data_source_type] || m.data_source_type || 'manual')})`).join('、');
            if (machines.length > 3) machineTxt += ` 等 ${machines.length} 台`;
        }
        const activeMachines = (machines || []).filter(m => m.lifecycle_status === 'active');
        const dsTypes = machines
            ? activeMachines.map(m => m.data_source_type || 'manual')
            : [p.data_source_type || 'manual'];
        // 按钮显隐按机器判定：只要有一台手动录入就能录入，只要有一台自动源就能采集
        const hasManualMachine = dsTypes.some(t => t === 'manual');
        const hasAutoMachine = dsTypes.some(t => t !== 'manual');
        // 可回溯 = 有 api 数据源机器，或有配了「API 回溯」的机器
        const hasHistoryMachine = activeMachines.some(m =>
            m.data_source_type === 'api' || m.backfill_method === 'api');
        // 可用碳资产直接用后端算好的值：口径统一在 project_manager.effective_available_net，
        // 前端不再自己兜底。以前这里是 p.available_assets_net || max(total-traded,0)，
        // 会把「已核证的都卖完了、可用确实是 0」当成缺数据补一笔（旺鑫因此多出 1.18 万）。
        html += `<div class="project-card${p.lifecycle_status !== 'active' ? ' project-voided' : ''}">
            ${p.lifecycle_status !== 'active' ? '<div class="void-banner">项目已作废 · 历史资料只读</div>' : ''}
            <div class="proj-header">
                <div>
                    <span class="proj-name">${applied ? '✅' : '○'} ${p.project_name}</span>
                    ${p.project_code ? `<span class="proj-code">${p.project_code}</span>` : ''}
                </div>
            </div>
            <div class="proj-meta">
                <span>🏷 类型: ${p.project_type || p.hook_name}</span>
                <span>🏭 ${machineCount} 台机器${machineTxt ? '：' + machineTxt : ''}</span>
                <span>💰 总碳资产: ${UI.fmtNum(p.total_carbon_assets, 2)} tCO₂e</span>
                <span>📦 可用: ${UI.fmtNum(p.available_assets_net || 0, 2)} tCO₂e</span>
                <span>📈 全历史累计减排: ${UI.fmtNum(p.lifetime_reduction || 0, 2)} tCO₂e</span>
                <span>🕓 待核证: ${p.pending_status === 'unknown' ? '待核对' : UI.fmtNum(p.pending_verification_cumulative || 0, 2) + ' tCO₂e' + (p.pending_status === 'stale' ? '（同步过期）' : '')}</span>
                ${(p.monitoring_anomaly_count || 0) > 0 ? `<button class="btn btn-sm btn-outline" onclick="App.openDataModal(${p.id}, true)">⚠ 受影响读数 ${p.monitoring_anomaly_count} 条${p.sampling_conflict_count ? '（疑似日/分钟顺序冲突 ' + p.sampling_conflict_count + ' 条）' : ''}</button>` : ''}
                ${(p.monitoring_gap_count || 0) > 0 ? `<button class="btn btn-sm btn-outline" onclick="App.openDataModal(${p.id})">⚠ 历史缺口 ${p.monitoring_gap_count} 处</button>` : ''}
                ${p.pending_warnings && p.pending_warnings !== '[]' ? `<span class="muted" title="涉及核查覆盖、边界读数、设备读数或接口同步；点击读数按钮核对设备问题">待核证计算附有提醒</span>` : ''}
            </div>
            ${UI.stageDots(p)}
            <div class="proj-actions">
                <button class="btn btn-sm btn-outline" onclick="App.openDataModal(${p.id})">📊 数据</button>
                ${p.lifecycle_status === 'active' ? `
                <button class="btn btn-sm btn-outline" onclick="App.editProject(${p.id})">✏️ 编辑</button>
                <button class="btn btn-sm btn-outline" onclick="App.syncProject(${p.id})">🔄 同步</button>
                ${!p.project_code ? `<button class="btn btn-sm btn-outline" title="预分配 VCER 编号（先预订后转正，PDD 可直接使用该编号编制）" onclick="App.reserveCode(${p.id})">🔢 预分配编号</button>` : ''}
                ${(state.gapCounts[p.id] || 0) > 0 ? `<button class="btn btn-sm btn-danger" title="检测到采集断档，点开看会补记多少、确认后再写入" onclick="App.openGapList(${p.id})">⚠ 断档兜底 (${state.gapCounts[p.id]})</button>` : ''}
                ${hasManualMachine ? `<button class="btn btn-sm btn-outline" onclick="App.openManualData(${p.id})">📝 录入数据</button>` : ''}
                ${hasAutoMachine ? `<button class="btn btn-sm btn-outline" onclick="App.collectNow(${p.id})">📡 采集</button>` : ''}
                ${hasHistoryMachine ? `<button class="btn btn-sm btn-outline" title="从基准日期到当前全量补拉历史数据（幂等，已存在的自动跳过）" onclick="App.openBackfillModal(${p.id})">⏳ 回溯历史</button>` : ''}
                <button class="btn btn-sm btn-primary" onclick="App.openReportForProject(${p.id})">📄 去报告中心</button>
                <button class="btn btn-sm btn-danger" onclick="App.deleteProject(${p.id})">🗑 删除</button>
                <button class="btn btn-sm btn-danger" onclick="App.voidProject(${p.id})">作废项目</button>` : `
                <button class="btn btn-sm btn-outline" onclick="App.showProjectArchive(${p.id})">查看只读档案</button>
                ${p.lifecycle_status === 'archive_incomplete' ? `<button class="btn btn-sm btn-outline" onclick="App.retryProjectArchive(${p.id})">重试归档</button>` : ''}
                <button class="btn btn-sm btn-outline" onclick="App.restoreProject(${p.id})">恢复使用</button>`}
            </div>
        </div>`;
    });
    container.innerHTML = html;
}

async function voidProject(projectId) {
    const reason = prompt('请填写作废原因。作废后立即停止采集、回溯及新提交；历史数据与促进会编号保留。');
    if (!reason || !reason.trim()) return;
    if (!confirm('确认作废该项目并开始归档促进会历史记录与附件？')) return;
    const result = await API.post('/api/projects/' + projectId + '/lifecycle/void', {reason: reason.trim()});
    alert(result.status === '归档未完成'
        ? '项目已停用，但归档未完成：' + (result.errors || []).join('；')
        : (result.msg || result.status));
    await loadProjects();
}

async function restoreProject(projectId) {
    const reason = prompt('恢复前请核对站点映射、设备状态及核查范围，并填写恢复原因。停用期间的数据不会自动补采。');
    if (!reason || !reason.trim()) return;
    const result = await API.post('/api/projects/' + projectId + '/lifecycle/restore', {reason: reason.trim()});
    alert(result.msg || result.status);
    await loadProjects();
}

async function retryProjectArchive(projectId) {
    const result = await API.post('/api/projects/' + projectId + '/lifecycle/archive/retry', {});
    alert(result.status === '归档未完成' ? (result.errors || []).join('；') : '归档完成');
    await loadProjects();
}

async function showProjectArchive(projectId) {
    const data = await API.get('/api/projects/' + projectId + '/lifecycle/archive');
    const archive = data.archive || {};
    let manifest = {};
    try { manifest = JSON.parse(archive.manifest_json || '{}'); } catch (e) {}
    let errors = [];
    try { errors = JSON.parse(archive.errors_json || '[]'); } catch (e) {}
    const modal = document.getElementById('projectArchiveModal');
    const content = document.getElementById('projectArchiveContent');
    const fileRows = (manifest.files || []).map(f =>
        '<li>' + _esc(f.field) + ' · ' + _esc(f.size) + ' 字节 · SHA-256 ' +
        _esc(f.sha256) + ' <a href="/api/projects/' + projectId +
        '/lifecycle/archive/file/' +
        encodeURIComponent(f.archive_path.split('/').pop()) + '">下载</a></li>').join('');
    const stageData = (manifest.stages || {}).project || {};
    const stages = [1,2,3,4,5].map(n =>
        '<li>阶段 ' + n + '：' + _esc(stageData['stage' + n + '_status'] || '未返回') + '</li>').join('');
    const rounds = Object.entries(manifest.rounds || {}).flatMap(([stage, response]) =>
        (response.rounds || []).map(r => '<tr><td>' + _esc(stage) + '</td><td>' +
            _esc(r.round || '') + '</td><td>' + _esc(r.status || '') + '</td><td>' +
            _esc(r.verify_start_date || '') + '—' + _esc(r.verify_end_date || '') +
            '</td><td>' + _esc(r.verified_reduction || r.traded_amount || r.offset_amount || '') +
            '</td></tr>')).join('');
    const reports = (manifest.reports || []).map(r =>
        '<li>报告 #' + _esc(r.id) + ' · ' + _esc(r.report_type || '') +
        ' · ' + _esc(r.generated_at || '') + '</li>').join('');
    content.innerHTML =
        '<p>归档状态：' + _esc(archive.status || '未归档') + '</p>' +
        (errors.length ? '<p class="text-danger">失败项：' + errors.map(_esc).join('；') + '</p>' : '') +
        '<h3>促进会阶段</h3><ul>' + (stages || '<li>暂无</li>') + '</ul>' +
        '<h3>促进会轮次</h3><div style="overflow:auto"><table><thead><tr><th>阶段</th><th>轮次</th><th>状态</th><th>日期</th><th>金额/数量</th></tr></thead><tbody>' +
        (rounds || '<tr><td colspan="5">暂无轮次</td></tr>') + '</tbody></table></div>' +
        '<h3>本地报告</h3><ul>' + (reports || '<li>无</li>') + '</ul>' +
        '<h3>归档文件</h3><ul>' + (fileRows || '<li>无附件</li>') + '</ul>' +
        '<h3>操作记录</h3><ul>' + (data.events || []).map(e =>
            '<li>' + _esc(e.created_at) + ' · ' + _esc(e.action) +
            ' · ' + _esc(e.reason) + '</li>').join('') + '</ul>';
    modal.classList.remove('hidden');
}

// ═══════════════════════════════════════════════════════════
// 机器（多设备）
// 一个项目下可以挂多台机器，数据源类型/数据源配置/公式参数都在机器上，
// 监测数据也按 machine_id 归属。弹窗里就地增删改，改完即时入库。
// ═══════════════════════════════════════════════════════════

// 弹窗内机器列表的临时状态。只在项目弹窗打开期间有效。
const machineState = { projectId: null, list: [], editingId: null, loading: false };

// 后端把 JSON 存成字符串返回，展示前尽量格式化；解析不了就原样给用户改
function _prettyJson(v) {
    if (v === null || v === undefined || v === '') return '';
    let p = v;
    if (typeof p === 'string') {
        try { p = JSON.parse(p); } catch(e) { return v; }
    }
    // 有些历史数据是"字符串套字符串"，多解一层
    if (typeof p === 'string') { try { p = JSON.parse(p); } catch(e) {} }
    try { return JSON.stringify(p, null, 2); } catch(e) { return String(v); }
}

function _machineDsJson(v, type) {
    let cfg = v;
    if (typeof cfg === 'string') { try { cfg = JSON.parse(cfg || '{}'); } catch(e) { return v; } }
    cfg = { ...(cfg || {}) };
    delete cfg.normalizer;
    delete cfg.request;
    delete cfg.headers;
    delete cfg.aliases;
    if (type === 'api') {
        delete cfg.site_id;
        delete cfg.poll_interval_seconds;
    }
    return _prettyJson(cfg);
}

// textarea 自适应高度：内容有多少行就撑多高，不用手动拖
function autoResizeTA(el) {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = (el.scrollHeight + 2) + 'px';
}

// 对弹窗内所有 JSON textarea 做一次自适应（渲染后调用）
function autoResizeMachineTextareas() {
    const listEl = document.getElementById('machineList');
    if (!listEl) return;
    listEl.querySelectorAll('textarea.mono').forEach(autoResizeTA);
}

// hook_params.has_oil 可能是 true/false、1/0、"有油" 等写法，统一成人话
function _oilText(v) {
    const s = String(v).toLowerCase();
    if (v === true || s === '1' || s === 'true' || s === '有油' || s === 'yes') return '有油';
    if (v === false || s === '0' || s === 'false' || s === '无油' || s === 'no') return '无油';
    return String(v);
}

// 行摘要：数据源类型 [· 有油/无油] [· 前置转换] [· 回溯方式] · N 条数据
const DS_TYPE_LABELS = { manual: '手动录入', mqtt: 'MQTT', api: 'API 定时拉取' };
const BACKFILL_LABELS = { none: '', api: '回溯:API', manual: '回溯:手动' };
function machineMeta(m) {
    const parts = [DS_TYPE_LABELS[m.data_source_type] || m.data_source_type || '手动录入'];
    let hp = m.hook_params;
    if (typeof hp === 'string') { try { hp = JSON.parse(hp || '{}'); } catch(e) { hp = null; } }
    if (hp && typeof hp === 'object' && hp.has_oil !== undefined && hp.has_oil !== null && hp.has_oil !== '') {
        parts.push(_oilText(hp.has_oil));
    }
    // 配了前置转换就标出来
    let cfg = m.data_source_config;
    if (typeof cfg === 'string') { try { cfg = JSON.parse(cfg || '{}'); } catch(e) { cfg = null; } }
    let norm = m.normalizer_config;
    if (typeof norm === 'string') { try { norm = JSON.parse(norm || '{}'); } catch(e) { norm = {}; } }
    norm = norm && norm.hook ? norm : (cfg && cfg.normalizer);
    if (norm && norm.hook) parts.push(`前置转换:${norm.hook}`);
    // 回溯方式（api 机器天然可回溯；mqtt 机器看 backfill_method）
    const bm = m.backfill_method || (m.data_source_type === 'api' ? 'api' : 'none');
    if (BACKFILL_LABELS[bm]) parts.push(BACKFILL_LABELS[bm]);
    parts.push(`${m.data_count || 0} 条数据`);
    return parts.join(' · ');
}

function machineRowHtml(m) {
    const machineVoided = m.lifecycle_status !== 'active';
    // 编辑态就在本行下方内联展开，同一时刻只会有一行在编辑，id 可以写死
    if (machineState.editingId === m.id) {
        return `<div class="machine-row editing">
            <div class="machine-editor-head">
                <div><strong>编辑机器 · ${_esc(m.name)}</strong><small>机器编号 #${m.id} · 配置仅作用于这一台设备</small></div>
                <button type="button" class="btn btn-sm btn-outline" onclick="App.machineCancelEdit()">关闭</button>
            </div>
            <div class="machine-edit-form">
                <section class="machine-editor-section">
                    <div class="machine-section-title"><span>01</span><div><strong>设备身份</strong><small>先确认设备名称和日常数据来源</small></div></div>
                    <div class="machine-form-grid">
                        <div class="form-group">
                            <label for="mEditName">机器名称 *</label>
                            <input type="text" id="mEditName" class="input" value="${_esc(m.name)}">
                        </div>
                        <div class="form-group">
                            <label for="mEditType">数据源类型 *</label>
                            <select id="mEditType" class="input custom-select" onchange="App.machineSourceToggle()">
                                <option value="manual">手动录入</option>
                                <option value="mqtt">MQTT 实时上报</option>
                                <option value="api">API 定时拉取</option>
                            </select>
                        </div>
                    </div>
                </section>

                <section class="machine-editor-section" id="mEditDsGroup" style="display:none">
                    <div class="machine-section-title"><span>02</span><div><strong>数据来源</strong><small>机器 ID 和采集节奏按设备分别填写</small></div></div>
                    <div id="mEditApiFields" class="machine-form-grid" style="display:none">
                        <div class="form-group">
                            <label for="mEditSiteId">平台站点 / 机器 ID（dataId）</label>
                            <div class="machine-inline-control">
                                <input type="text" id="mEditSiteId" class="input mono" placeholder="由数据提供方确认">
                                <button type="button" class="btn btn-outline btn-sm" id="btnListSites" onclick="App.openSiteListModal()">查站点</button>
                            </div>
                            <small>这是平台识别当前机器的 ID，每台机器不同；通常会作为请求中的 {site_id} 注入。请先核对项目和设备对应关系；留空时这台 API 机器不能自动采集。</small>
                        </div>
                        <div class="form-group" id="mEditPollWrap">
                            <label for="mEditPollInterval">自动拉取频率</label>
                            <select id="mEditPollInterval" class="input custom-select">
                                <option value="">跟随转换文件声明的默认值</option>
                                <option value="3600">每小时</option>
                                <option value="21600">每 6 小时</option>
                                <option value="86400">每天</option>
                                <option value="1296000">每半月（15 天）</option>
                                <option value="2592000">每月（30 天）</option>
                            </select>
                            <small>多久主动向历史 API 拉取一次数据。</small>
                        </div>
                    </div>
                    <details class="machine-advanced" id="mEditDsAdvanced">
                        <summary id="mEditDsAdvancedTitle">其他数据源参数（JSON）</summary>
                        <p id="mEditDsHelp">仅在需要调整粒度、分块、超时等参数时填写。</p>
                        <textarea id="mEditDsConfig" class="input mono" oninput="App.autoResizeTA(this)" rows="3" placeholder='{"record_time_type":"normal","chunk_days":28}'>${_esc(_machineDsJson(m.data_source_config, m.data_source_type))}</textarea>
                    </details>
                </section>

                <section class="machine-editor-section" id="mEditRequestWrap" style="display:none">
                    <div class="machine-section-title"><span>03</span><div><strong>API 取数与回溯</strong><small>接口协议统一由前置转换文件处理</small></div></div>
                    <div class="machine-url-notice">在「设置 → 企业配置」填写历史数据 API 基地址、账号和密码；在本机填写上方的站点 / 机器 ID，并在下方选择本企业的转换文件。登录、Token、请求路径与请求体、响应解析都由 企业包内的转换文件 处理。URL 问号后的 <code>?A=1&amp;B=2</code> 叫查询参数（Query Params）；当前接口由转换文件构造，不需在机器里填写。新接口有变化时由技术人员修改该文件。</div>
                    <div class="machine-form-grid">
                        <div class="form-group">
                            <label for="mEditBeginSource">历史回溯起点</label>
                            <select id="mEditBeginSource" class="input custom-select" onchange="App.machineBackfillSourceToggle()">
                                <option value="baseline">项目基准日期</option>
                                <option value="custom">本机指定日期</option>
                            </select>
                            <small>回溯弹窗手填日期优先；日常采集仍按最近窗口取数。</small>
                        </div>
                        <div class="form-group" id="mEditBeginAtWrap" style="display:none">
                            <label for="mEditBeginAt">本机指定日期</label>
                            <input type="date" id="mEditBeginAt" class="input">
                        </div>
                    </div>
                    <div class="machine-request-test">
                        <div class="machine-section-title"><div><strong>测试请求</strong><small>使用当前未保存的机器 ID 和转换文件请求一次，不写入监测数据。</small></div></div>
                        <div class="machine-form-grid">
                            <div class="form-group"><label for="mEditTestBegin">开始时间</label><input type="datetime-local" id="mEditTestBegin" class="input"></div>
                            <div class="form-group"><label for="mEditTestEnd">结束时间</label><input type="datetime-local" id="mEditTestEnd" class="input"></div>
                        </div>
                        <button type="button" id="mEditTestButton" class="btn btn-outline btn-sm" onclick="App.machineApiTest(${m.id})">发送测试请求</button>
                        <small>显示 HTTP 状态码和原始响应。默认最近采集窗口；可改成历史日期检查回溯接口。</small>
                        <div id="mEditTestResult" class="machine-test-result hidden" role="status" aria-live="polite"></div>
                    </div>
                </section>

                <section class="machine-editor-section" id="mEditNormGroup" style="display:none">
                    <div class="machine-section-title"><span>04</span><div><strong>前置转换</strong><small>把平台报文转换为计算公式需要的字段</small></div></div>
                    <label class="switch-row machine-norm-switch">
                        <span class="switch"><input type="checkbox" id="mEditNormOn" onchange="App.machineNormToggle()"><span class="switch-slider"></span></span>
                        <span class="switch-label">开启格式转换</span>
                    </label>
                    <div id="mEditNormHookWrap" class="machine-form-grid" style="display:none">
                        <div class="form-group">
                            <label for="mEditNormHook">转换文件</label>
                            <select id="mEditNormHook" class="input custom-select" data-placeholder="-- 选择转换文件 --" onchange="App.machineNormalizerSelected()"></select>
                            <small>选择本企业数据平台对应的转换文件；接口响应变化时由技术人员在企业包内修改或新增文件。</small>
                        </div>
                        <div class="form-group">
                            <label for="mEditNormParams">转换参数（可选，JSON）</label>
                            <textarea id="mEditNormParams" class="input mono" oninput="App.autoResizeTA(this)" rows="2" placeholder="{}"></textarea>
                        </div>
                    </div>
                </section>

                <section class="machine-editor-section">
                    <div class="machine-section-title"><span>05</span><div><strong>计算参数</strong><small>这台机器参与减排计算的设备参数</small></div></div>
                    <div class="form-group">
                        <label for="mEditHookParams">公式参数（JSON）</label>
                        <textarea id="mEditHookParams" class="input mono" oninput="App.autoResizeTA(this)" rows="3" placeholder='{"dew_point":0,"rated_pressure":0.3}'>${_esc(_prettyJson(m.hook_params))}</textarea>
                        <small id="mEditHookHint"></small>
                    </div>
                </section>

                <section class="machine-editor-section" id="mEditBackfillGroup" style="display:none">
                    <div class="machine-section-title"><span>06</span><div><strong>历史回溯</strong><small>MQTT 机器可另用 API 补录历史；API 机器直接使用自身配置回溯</small></div></div>
                    <div class="form-group">
                        <label for="mEditBackfillMethod">回溯方式</label>
                        <select id="mEditBackfillMethod" class="input custom-select" onchange="App.machineBackfillToggle()">
                            <option value="none">无（不回溯）</option>
                            <option value="api">API 回溯</option>
                            <option value="manual">手动录入回溯</option>
                        </select>
                    </div>
                    <div class="form-group" id="mEditBackfillCfgWrap" style="display:none">
                        <label for="mEditBackfillConfig">API 回溯配置（JSON）</label>
                        <textarea id="mEditBackfillConfig" class="input mono" oninput="App.autoResizeTA(this)" rows="3" placeholder='{"site_id":"1129","record_time_type":"month"}'></textarea>
                    </div>
                </section>
                <div class="machine-edit-actions">
                    <button type="button" class="btn btn-sm btn-primary" onclick="App.machineSave(${m.id})">保存机器</button>
                    <button type="button" class="btn btn-sm btn-outline" onclick="App.machineCancelEdit()">取消</button>
                </div>
            </div>
        </div>`;
    }
    return `<div class="machine-row${machineVoided ? ' machine-voided' : ''}">
        <span class="machine-row-name">${_esc(m.name)}</span>
        ${machineVoided ? `<span class="tag">已作废 · ${_esc(m.void_reason || '设备已撤走')}</span>
            <button class="btn btn-sm btn-outline" onclick="App.machineRestore(${m.id})">恢复使用</button>
            <button class="btn btn-sm btn-outline" onclick="App.machineHistory(${m.id})">操作记录</button>` : `
            <button class="btn btn-sm btn-outline" onclick="App.machineToggleEdit(${m.id})">编辑</button>
            ${m.data_source_type === 'manual' ? `<button class="btn btn-sm btn-outline" onclick="App.openManualData(${machineState.projectId}, ${m.id})">📝 录入</button>` : ''}
            <button class="btn btn-sm btn-outline" onclick="App.openImportModal(${machineState.projectId}, ${m.id})">📥 导入</button>
            <button class="btn btn-sm btn-danger" onclick="App.machineVoid(${m.id})">作废机器</button>
            <button class="btn btn-sm btn-danger" onclick="App.machineDelete(${m.id})">删除</button>`}
        <span class="machine-row-meta">${_esc(machineMeta(m))}</span>
    </div>`;
}

async function machineVoid(machineId) {
    const reason = prompt('请填写机器撤走或停用原因。作废后停止该机器采集与回溯，保留原有数据。');
    if (!reason || !reason.trim()) return;
    const projectId = machineState.projectId;
    const result = await API.post('/api/projects/' + projectId +
        '/machines/' + machineId + '/void', {reason: reason.trim()});
    alert(result.msg || result.status);
    if (result.status === '成功') {
        machineState.editingId = null;
        await loadProjectMachines(projectId);
        await loadProjects();
    }
}

async function machineRestore(machineId) {
    const reason = prompt('恢复前请核对气站与表计归属、设备状态和核查范围；请输入恢复原因。');
    if (!reason || !reason.trim()) return;
    const projectId = machineState.projectId;
    const result = await API.post('/api/projects/' + projectId +
        '/machines/' + machineId + '/restore', {reason: reason.trim()});
    alert(result.msg || result.status);
    if (result.status === '成功') {
        await loadProjectMachines(projectId);
        await loadProjects();
    }
}

async function machineHistory(machineId) {
    const data = await API.get('/api/projects/' + machineState.projectId +
        '/machines/' + machineId + '/lifecycle');
    alert((data.events || []).map(e => e.created_at + ' · ' + e.action + ' · ' + e.reason)
        .join('\\n') || '暂无操作记录');
}

function renderMachineBlock() {
    const listEl = document.getElementById('machineList');
    if (!listEl) return;
    const hintEl = document.getElementById('machineHint');
    const addBtn = document.getElementById('btnAddMachine');

    if (!machineState.projectId) {
        // 新建项目时连项目 id 都没有，机器接口无从调起，先把预期告诉用户
        listEl.innerHTML = '<div class="machine-empty">项目保存后将自动创建「1号机器」，可再编辑其数据源与公式参数</div>';
        if (hintEl) hintEl.textContent = '';
        if (addBtn) addBtn.classList.add('hidden');
        return;
    }
    if (addBtn) addBtn.classList.remove('hidden');
    if (machineState.loading) {
        listEl.innerHTML = '<div class="machine-empty">机器加载中…</div>';
        if (hintEl) hintEl.textContent = '';
        return;
    }
    if (!machineState.list.length) {
        listEl.innerHTML = '<div class="machine-empty">该项目还没有机器，点「+ 加机器」新建一台</div>';
        if (hintEl) hintEl.textContent = '';
        return;
    }
    listEl.innerHTML = machineState.list.map(machineRowHtml).join('');
    if (hintEl) hintEl.textContent = '每台机器各自一套数据源与公式参数，采集/录入的数据按机器分开存放。';

    if (machineState.editingId !== null) {
        // 编辑行的下拉是刚注入的新 DOM，得先建 CustomSelect 实例再把值刷上去
        CustomSelect.initAll(listEl);
        const typeEl = document.getElementById('mEditType');
        const editing = machineState.list.find(x => x.id === machineState.editingId);
        if (typeEl && editing) {
            typeEl.value = editing.data_source_type || 'manual';
            CustomSelect.refresh(typeEl);
        }
        machineSourceToggle();
        machineHookHint();
        machineNormFill(editing);
        machineApiFill(editing);
        machinePollFill(editing);
        machineBackfillFill(editing);
        autoResizeMachineTextareas();
    }
}

async function loadProjectMachines(pid) {
    machineState.loading = true;
    renderMachineBlock();
    try {
        const data = await API.get(`/api/projects/${pid}/machines`);
        // 请求期间用户可能关了弹窗或换了项目，过期响应直接丢弃
        if (machineState.projectId !== pid) return;
        machineState.list = (data && data.machines) || [];
    } catch(e) {
        if (machineState.projectId !== pid) return;
        machineState.list = [];
    }
    machineState.loading = false;
    renderMachineBlock();
}

function machineToggleEdit(mid) {
    machineState.editingId = machineState.editingId === mid ? null : mid;
    renderMachineBlock();
}

function machineCancelEdit() {
    machineState.editingId = null;
    renderMachineBlock();
}

// 只在类型 ≠ manual 时才需要填数据源配置
function machineSourceToggle() {
    const type = document.getElementById('mEditType')?.value || 'manual';
    const show = (id, visible) => {
        const el = document.getElementById(id);
        if (el) el.style.display = visible ? '' : 'none';
    };
    show('mEditDsGroup', type !== 'manual');
    show('mEditApiFields', type === 'api');
    show('mEditRequestWrap', type === 'api');
    show('mEditNormGroup', type === 'api' || type === 'mqtt');
    show('mEditBackfillGroup', type === 'mqtt');
    const advanced = document.getElementById('mEditDsAdvanced');
    if (advanced) advanced.open = type === 'mqtt';
    const title = document.getElementById('mEditDsAdvancedTitle');
    if (title) title.textContent = type === 'mqtt'
        ? 'MQTT 连接配置（JSON）' : '其他 API 参数（JSON，可选）';
    const help = document.getElementById('mEditDsHelp');
    if (help) help.textContent = type === 'mqtt'
        ? '填写 broker_host、broker_port、topic 等 MQTT 连接参数。'
        : '仅在需要调整粒度、分块、超时等参数时填写；站点 ID 和频率在上方填写。';
}

// ── 获取站点列表（dataId 选择，api 机器用）──
let siteProjectsCache = [];   // 最近一次拉到的项目列表（含 province/city）
let siteProjectsMeta = '';    // 缓存状态文案（来自后端 cached/fetched_at）

async function _loadSites(refresh) {
    const hookSel = document.getElementById('mEditNormHook');
    const hook = hookSel ? hookSel.value : '';
    if (!hook) { alert('请先在本机「前置转换」中选择本企业的转换文件'); return; }
    const body = document.getElementById('siteListBody');
    body.innerHTML = '<p class="muted">加载中...</p>';
    document.getElementById('siteListModal').classList.remove('hidden', 'closing');
    try {
        const resp = await API.post('/api/normalizer/list-projects', { hook, refresh });
        if (resp.status !== '成功') {
            body.innerHTML = `<p class="muted">${_esc(resp.msg || '获取失败')}</p>`;
            return;
        }
        siteProjectsCache = resp.projects || [];
        siteProjectsMeta = resp.cached
            ? `已用本地缓存（拉取于 ${resp.fetched_at || '—'}），点「刷新」重新拉取`
            : '已从平台重新拉取';
        const warnings = resp.mapping_warnings || [];
        if (warnings.length) {
            siteProjectsMeta += '。' + warnings.length + ' 台机器站点映射待核实：' +
                warnings.map(w => w.project_name + ' / ' + w.machine_name + '（' + w.reason + '）').join('；') +
                '。平台列表缺项不会自动作废项目。';
        }
        renderSiteList();
    } catch(e) {
        body.innerHTML = `<p class="muted">获取失败: ${_esc(e.message)}</p>`;
    }
}

function openSiteListModal() {
    document.getElementById('siteSearch').value = '';
    _loadSites(false);
}

function refreshSites() {
    _loadSites(true);
}

function filterSites() {
    renderSiteList();
}

function renderSiteList() {
    const body = document.getElementById('siteListBody');
    const meta = document.getElementById('siteListMeta');
    if (meta) meta.textContent = siteProjectsMeta;
    const kw = (document.getElementById('siteSearch').value || '').trim().toLowerCase();
    const list = siteProjectsCache.filter(p => {
        if (!kw) return true;
        const name = String(p.projectName || '').toLowerCase();
        return name.includes(kw) || String(p.dataId).includes(kw)
            || String(p.province || '').toLowerCase().includes(kw)
            || String(p.city || '').toLowerCase().includes(kw);
    });
    if (!list.length) { body.innerHTML = '<p class="muted">无匹配项目</p>'; return; }
    // 按省分组（省内显示城市），未分类的放最后
    const groups = {};
    list.forEach(p => { const prov = p.province || '未分类'; (groups[prov] = groups[prov] || []).push(p); });
    const order = Object.keys(groups).sort((a, b) => (a === '未分类' ? 1 : b === '未分类' ? -1 : a.localeCompare(b, 'zh')));
    let html = '';
    order.forEach(prov => {
        html += `<div style="font-weight:bold;margin:10px 0 4px;color:var(--mint-dark)">📍 ${_esc(prov)}</div>`;
        groups[prov].forEach(p => {
            const cityTag = p.city ? ` <span class="muted">${_esc(p.city)}</span>` : '';
            html += `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 6px 6px 16px;cursor:pointer;border-bottom:1px solid rgba(0,0,0,.04)"
                        onclick="App.selectSite('${_esc(String(p.dataId))}')">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b>${_esc(p.projectName || ('站点 ' + p.dataId))}</b>${cityTag}</span>
                <span class="muted" style="flex-shrink:0">${_esc(String(p.dataId))}</span></div>`;
        });
    });
    body.innerHTML = html;
}

function closeSiteListModal() {
    const modal = document.getElementById('siteListModal');
    if (modal) modal.classList.add('hidden');
}

function selectSite(dataId) {
    const siteInput = document.getElementById('mEditSiteId');
    if (siteInput) siteInput.value = String(dataId);
    closeSiteListModal();
}

// ── 前置转换（normalizer）──
// 开关 + 选文件。转换器列表懒加载缓存；保存时按开关决定是否写 normalizer
let _normalizerCache = null;

async function loadNormalizerOptions() {
    if (_normalizerCache === null) {
        try {
            const resp = await API.get('/api/normalizers');
            _normalizerCache = (resp && resp.normalizers) || [];
        } catch(e) { _normalizerCache = []; }
    }
    const cur = document.getElementById('mEditNormHook');
    if (!cur) return;
    _renderNormalizerSelect(cur.dataset.selected || cur.value);
}

// 把转换器选项渲染进下拉，并选中 selectedName（有值才选）。同步，可直接调用。
function _renderNormalizerSelect(selectedName) {
    const cur = document.getElementById('mEditNormHook');
    if (!cur) return;
    const list = _normalizerCache || [];
    let html = '<option value="">（选择转换文件）</option>';
    list.forEach(n => {
        html += `<option value="${_esc(n.name)}" title="${_esc(n.description || '')}">${_esc(n.label || n.name)}</option>`;
    });
    // 兜底：已保存的 hook 不在列表里（缓存为空/未加载/文件被删），也补一个同名 option 保证能回显
    if (selectedName && !list.some(n => n.name === selectedName)) {
        html += `<option value="${_esc(selectedName)}">${_esc(selectedName)}</option>`;
    }
    if (!list.length && !selectedName) html = '<option value="">（无可用转换文件）</option>';
    cur.innerHTML = html;
    if (selectedName) cur.value = selectedName;
    // 重建自定义下拉面板 + 同步触发器文字（refresh 只刷文字不重建选项）
    CustomSelect.rebuild(cur);
}

function machineNormalizerSelected() {
    const hook = document.getElementById('mEditNormHook')?.value;
    const on = document.getElementById('mEditNormOn');
    if (on && hook) { on.checked = true; machineNormToggle(); }
}

function machineNormToggle() {
    const on = document.getElementById('mEditNormOn');
    const wrap = document.getElementById('mEditNormHookWrap');
    if (!on || !wrap) return;
    wrap.style.display = on.checked ? 'block' : 'none';
    if (on.checked) loadNormalizerOptions();
}

// 把机器现有 data_source_config 里的 poll_interval_seconds 回填到「拉取频率」下拉
function machinePollFill(m) {
    const sel = document.getElementById('mEditPollInterval');
    if (!sel || !m) return;
    let cfg = m.data_source_config;
    if (typeof cfg === 'string') { try { cfg = JSON.parse(cfg || '{}'); } catch(e) { cfg = {}; } }
    cfg = cfg || {};
    const secs = cfg.poll_interval_seconds;
    if (Number(secs) > 0 && !Array.from(sel.options).some(option => option.value === String(secs))) {
        const option = new Option(`自定义：每 ${secs} 秒`, String(secs));
        sel.add(option);
        CustomSelect.rebuild(sel);
    }
    sel.value = (secs !== undefined && secs !== null) ? String(secs) : '';
    CustomSelect.refresh(sel);
}

// 把机器现有 data_source_config 里的 normalizer 块回填到开关 + 下拉
function machineNormFill(m) {
    const onEl = document.getElementById('mEditNormOn');
    if (!onEl || !m) return;
    let cfg = m.data_source_config;
    if (typeof cfg === 'string') { try { cfg = JSON.parse(cfg || '{}'); } catch(e) { cfg = {}; } }
    cfg = cfg || {};
    let norm = m.normalizer_config;
    if (typeof norm === 'string') { try { norm = JSON.parse(norm || '{}'); } catch(e) { norm = {}; } }
    if (!norm || !norm.hook) norm = cfg.normalizer || {};
    const hasHook = !!(norm && norm.hook);
    const selName = hasHook ? norm.hook : '';
    onEl.checked = hasHook;
    const wrap = document.getElementById('mEditNormHookWrap');
    if (wrap) wrap.style.display = hasHook ? 'block' : 'none';
    const hookSel = document.getElementById('mEditNormHook');
    if (hookSel) hookSel.dataset.selected = selName;
    const paramsEl = document.getElementById('mEditNormParams');
    if (paramsEl) paramsEl.value = (hasHook && norm.params) ? JSON.stringify(norm.params, null, 2) : '';
    // 选项已缓存就同步回填（直接传 selName，不依赖 dataset.selected 间接层）；否则异步加载后回填
    if (_normalizerCache !== null) {
        _renderNormalizerSelect(selName);
    } else {
        loadNormalizerOptions();
    }
}

function machineApiFill(m) {
    let cfg = m.data_source_config;
    if (typeof cfg === 'string') { try { cfg = JSON.parse(cfg || '{}'); } catch(e) { cfg = {}; } }
    cfg = cfg || {};
    const siteInput = document.getElementById('mEditSiteId');
    if (siteInput) siteInput.value = cfg.site_id || '';
    const beginSource = document.getElementById('mEditBeginSource');
    if (beginSource) { beginSource.value = cfg.backfill_since ? 'custom' : 'baseline'; CustomSelect.refresh(beginSource); }
    const beginAt = document.getElementById('mEditBeginAt');
    if (beginAt) beginAt.value = (cfg.backfill_since || '').slice(0, 10);
    machineBackfillSourceToggle();
    const end = new Date();
    const minutes = Math.max(1, Number(cfg.lookback_minutes) || 60);
    document.getElementById('mEditTestEnd').value = _machineTestLocalTime(end);
    document.getElementById('mEditTestBegin').value = _machineTestLocalTime(new Date(end.getTime() - minutes * 60000));
}

function _machineTestLocalTime(date) {
    const p = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${p(date.getMonth()+1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`;
}

function machineBackfillSourceToggle() {
    const source = document.getElementById('mEditBeginSource');
    const wrap = document.getElementById('mEditBeginAtWrap');
    if (source && wrap) wrap.style.display = source.value === 'custom' ? '' : 'none';
}

async function machineApiTest(mid) {
    const result = document.getElementById('mEditTestResult');
    const button = document.getElementById('mEditTestButton');
    if (!result || !button) return;
    let source, normalizer;
    try {
        source = parseJsonField('mEditDsConfig');
        if (!source || Array.isArray(source) || typeof source !== 'object') throw new Error('其他 API 参数必须是 JSON 对象');
        const siteId = document.getElementById('mEditSiteId')?.value.trim() || '';
        if (!siteId) throw new Error('请先填写平台站点 / 机器 ID');
        source.site_id = siteId;
        delete source.request;
        delete source.headers;
        delete source.aliases;
        if (!document.getElementById('mEditNormOn')?.checked) throw new Error('请先开启前置转换并选择转换文件');
        const hook = document.getElementById('mEditNormHook')?.value || '';
        if (!hook) throw new Error('请先选择转换文件');
        normalizer = { hook };
        const params = document.getElementById('mEditNormParams')?.value.trim();
        if (params) normalizer.params = parseJsonField('mEditNormParams');
    } catch(e) {
        result.classList.remove('hidden');
        result.textContent = e.message;
        return;
    }
    const begin = document.getElementById('mEditTestBegin')?.value || '';
    const end = document.getElementById('mEditTestEnd')?.value || '';
    if (!begin || !end || begin >= end) {
        result.classList.remove('hidden');
        result.textContent = '请填写有效的测试时间，且结束时间晚于开始时间。';
        return;
    }
    button.disabled = true;
    result.classList.remove('hidden');
    result.textContent = '正在请求…';
    try {
        const http = await fetch(`/api/projects/${machineState.projectId}/machines/${mid}/test-request`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data_source_config: source, normalizer_config: normalizer, begin, end })
        });
        const resp = await http.json().catch(() => null);
        if (!resp) {
            result.textContent = http.status === 404
                ? '测试接口尚未加载。请在 PyCharm 中重启 app.py 服务后再试。'
                : `测试接口返回 HTTP ${http.status}，但响应不是 JSON。`;
            return;
        }
        if (resp.status !== '成功') {
            result.textContent = `测试失败：${resp.msg || '未知错误'}`;
            return;
        }
        let body = resp.response_body || '';
        try { body = JSON.stringify(JSON.parse(body), null, 2); } catch(e) {}
        result.textContent = `HTTP ${resp.status_code} · ${resp.method} · ${resp.begin} → ${resp.end}`
            + (resp.tested_alias ? `\n测试指标：${resp.tested_field} / ${resp.tested_alias}` : '')
            + (resp.truncated ? '\n响应超过 64 KB，下面仅显示前 64 KB。' : '')
            + `\n\n${body || '（响应体为空）'}`;
    } catch(e) {
        result.textContent = `测试请求未完成：${e.message}`;
    } finally {
        button.disabled = false;
    }
}

// ── 历史回溯方式 ──
function machineBackfillToggle() {
    const sel = document.getElementById('mEditBackfillMethod');
    const wrap = document.getElementById('mEditBackfillCfgWrap');
    if (!sel || !wrap) return;
    wrap.style.display = sel.value === 'api' ? 'block' : 'none';
}

// 把机器现有 backfill_method / backfill_config 回填到表单
function machineBackfillFill(m) {
    const sel = document.getElementById('mEditBackfillMethod');
    if (!sel || !m) return;
    // api 数据源天然可回溯，回溯方式显示 api；mqtt 机器看 backfill_method
    let method = m.backfill_method || 'none';
    if (m.data_source_type === 'api') method = 'api';
    sel.value = method;
    CustomSelect.refresh(sel);
    const cfgEl = document.getElementById('mEditBackfillConfig');
    if (cfgEl) cfgEl.value = _prettyJson(m.backfill_config);
    machineBackfillToggle();
}

// 公式参数该填什么取决于项目用的公式，参数提示从项目级挪到机器编辑表单里
async function machineHookHint() {
    const el = document.getElementById('mEditHookHint');
    if (!el) return;
    const hookName = document.getElementById('projHook').value;
    if (!hookName) { el.textContent = ''; return; }
    el.textContent = '加载参数说明…';
    try {
        const resp = await API.get(`/api/formulas/${hookName}/params`);
        // 期间可能已经切走/重渲染，元素换了就别写了
        if (document.getElementById('mEditHookHint') !== el) return;
        if (resp.status === '成功') {
            el.innerHTML = `需要参数: <strong>${(resp.required_params||[]).join(', ') || '无'}</strong><br>
                            需要数据字段: <strong>${(resp.required_raw_fields||[]).join(', ') || '无'}</strong>`;
        } else {
            el.textContent = '';
        }
    } catch(e) { el.textContent = ''; }
}

async function machineAdd() {
    const pid = machineState.projectId;
    if (!pid) return;
    const resp = await API.post(`/api/projects/${pid}/machines`, {});
    if (resp.status === '成功' && resp.machine) {
        machineState.list.push(resp.machine);
        machineState.editingId = resp.machine.id;   // 建完直接进编辑态，省一次点击
        renderMachineBlock();
    } else {
        alert('加机器失败: ' + ((resp && resp.msg) || '未知错误'));
    }
}

async function machineSave(mid) {
    const pid = machineState.projectId;
    if (!pid) return;
    const nameEl = document.getElementById('mEditName');
    const typeEl = document.getElementById('mEditType');
    if (!nameEl || !typeEl) return;
    const name = nameEl.value.trim();
    if (!name) return alert('机器名称不能为空');

    let dsConfig, hookParams, backfillMethod, backfillConfig;
    try {
        dsConfig = parseJsonField('mEditDsConfig');
        hookParams = parseJsonField('mEditHookParams');
        if (typeEl.value === 'api') {
            const siteId = document.getElementById('mEditSiteId')?.value.trim() || '';
            if (siteId) dsConfig.site_id = siteId;
            else delete dsConfig.site_id;
            const poll = document.getElementById('mEditPollInterval')?.value || '';
            if (poll) dsConfig.poll_interval_seconds = Number(poll);
            else delete dsConfig.poll_interval_seconds;
            const beginSource = document.getElementById('mEditBeginSource')?.value || 'baseline';
            if (beginSource === 'custom') {
                const beginAt = document.getElementById('mEditBeginAt')?.value || '';
                if (!beginAt) return alert('请选择本机历史回溯起点日期');
                dsConfig.backfill_since = beginAt;
            } else delete dsConfig.backfill_since;
        }

        // 新服务端独立保存；同时发送旧字段，让仍在运行的旧进程也能持久化。
        delete dsConfig.normalizer;
        if (typeEl.value === 'api') {
            delete dsConfig.request;
            delete dsConfig.headers;
            delete dsConfig.aliases;
        }
        var normalizerConfig = {};
        const onEl = document.getElementById('mEditNormOn');
        if (onEl && onEl.checked) {
            const hookSel = document.getElementById('mEditNormHook');
            const hookName = hookSel ? hookSel.value : '';
            if (!hookName) return alert('已开启前置转换，请选择一个转换文件');
            const norm = { hook: hookName };
            const paramsVal = document.getElementById('mEditNormParams');
            if (paramsVal && paramsVal.value.trim()) norm.params = parseJsonField('mEditNormParams');
            normalizerConfig = norm;
            dsConfig.normalizer = norm;
        }

        // 历史回溯方式
        const bfSel = document.getElementById('mEditBackfillMethod');
        backfillMethod = bfSel ? bfSel.value : 'none';
        backfillConfig = {};
        if (backfillMethod === 'api') {
            backfillConfig = parseJsonField('mEditBackfillConfig');
        }
    } catch(e) { return; }

    const resp = await API.put(`/api/projects/${pid}/machines/${mid}`, {
        name,
        data_source_type: typeEl.value,
        data_source_config: dsConfig,
        normalizer_config: normalizerConfig,
        hook_params: hookParams,
        backfill_method: backfillMethod,
        backfill_config: backfillConfig,
    });
    if (resp.status === '成功') {
        if (resp.machine) {
            let savedNorm = resp.machine.normalizer_config;
            if (typeof savedNorm === 'string') { try { savedNorm = JSON.parse(savedNorm || '{}'); } catch(e) { savedNorm = {}; } }
            let savedDs = resp.machine.data_source_config;
            if (typeof savedDs === 'string') { try { savedDs = JSON.parse(savedDs || '{}'); } catch(e) { savedDs = {}; } }
            const savedHook = savedNorm?.hook || savedDs?.normalizer?.hook || '';
            const requestedPoll = dsConfig.poll_interval_seconds ?? null;
            const savedPoll = savedDs?.poll_interval_seconds ?? null;
            if (typeEl.value === 'api' && String(savedPoll ?? '') !== String(requestedPoll ?? '')) {
                alert('机器已保存，但自动拉取频率未能正确写入。请刷新页面后重试，并检查服务端是否已更新。');
                return;
            }
            if (savedHook !== (normalizerConfig.hook || '')) {
                alert('机器已保存，但前置转换未能正确写入。请刷新页面后重试，并检查服务端是否已更新。');
                return;
            }
            const i = machineState.list.findIndex(x => x.id === mid);
            if (i >= 0) machineState.list[i] = resp.machine; else machineState.list.push(resp.machine);
        }
        machineState.editingId = null;
        renderMachineBlock();
        loadProjects();   // 卡片上的机器摘要与按钮显隐跟着变
    } else {
        alert('保存失败: ' + ((resp && resp.msg) || '未知错误'));
    }
}

async function machineDelete(mid) {
    const pid = machineState.projectId;
    if (!pid) return;
    const m = machineState.list.find(x => x.id === mid);
    const cnt = m ? (m.data_count || 0) : 0;
    // 删机器会一并删掉它名下的监测数据，先把台数和条数讲清楚
    if (!confirm(`确认删除机器「${(m && m.name) || ('#' + mid)}」？\n` +
        (cnt ? `该机器下 ${cnt} 条监测数据将一并删除。\n` : '') + '继续？')) return;

    const resp = await API.del(`/api/projects/${pid}/machines/${mid}`);
    if (resp.status === '成功') {
        machineState.list = machineState.list.filter(x => x.id !== mid);
        if (machineState.editingId === mid) machineState.editingId = null;
        renderMachineBlock();
        loadProjects();
        if (resp.deleted_data) showToast(`已删除机器，同时清理 ${resp.deleted_data} 条监测数据`);
    } else {
        alert('删除失败: ' + ((resp && resp.msg) || '未知错误'));
    }
}

// 把某个项目的机器填进下拉。只有一台机器也照常显示——用户得看得见采的是哪台。
async function fillMachineSel(sel, pid, placeholder, autoFirst) {
    if (!sel) return;
    sel.innerHTML = `<option value="">${placeholder}</option>`;
    if (pid) {
        try {
            const data = await API.get(`/api/projects/${pid}/machines`);
            const list = (data && data.machines) || [];
            list.forEach(m => { sel.innerHTML += `<option value="${m.id}">${_esc(m.name)}</option>`; });
            if (autoFirst && list.length) sel.value = String(list[0].id);
        } catch(e) {}
    }
    // 选项整批换了，必须同步自定义下拉，否则面板里还是旧选项
    CustomSelect.initAll(document);
    CustomSelect.refresh(sel);
}

// ═══════════════════════════════════════════════════════════
// 实时监测
// ═══════════════════════════════════════════════════════════

async function loadMonitorTab() {
    const sel = document.getElementById('monitorProjectSel');
    // 如果项目列表还未加载，主动拉取
    if (!state.projects.length) {
        try { const data = await API.get('/api/projects'); state.projects = data.projects || []; } catch(e) {}
    }
    // 填充选择器（保留第一个 "选择项目" 选项）
    while (sel.options.length > 1) sel.remove(1);
    state.projects.filter(p => p.lifecycle_status === 'active').forEach(p => {
        sel.innerHTML += `<option value="${p.id}" data-search-text="${_esc(p.project_name + " " + (p.project_code || ""))}">${_esc(p.project_name)}</option>`;
    });
    // 保持已选中的值
    if (state.selectedMonitorProject) sel.value = state.selectedMonitorProject;
    CustomSelect.refresh(sel);
    sel.onchange = () => {
        state.selectedMonitorProject = sel.value;
        if (state.selectedMonitorProject) { refreshMonitor(); startMonitorAutoRefresh(); }
    };
    // 如果已有选中项目，启动自动刷新
    if (sel.value) startMonitorAutoRefresh();
}

function startMonitorAutoRefresh() {
    // 清除旧定时器
    if (state.monitorTimer) clearInterval(state.monitorTimer);
    let countdown = 10;
    const cdEl = document.getElementById('refreshCountdown');
    if (cdEl) cdEl.textContent = countdown;
    state.monitorTimer = setInterval(() => {
        countdown--;
        if (cdEl) cdEl.textContent = countdown;
        if (countdown <= 0) {
            countdown = 10;
            refreshMonitor();
        }
    }, 1000);
}

let monitorRefreshSequence = 0;
let monitorMachineCache = {projectId: null, machines: []};
async function refreshMonitor() {
    const pid = state.selectedMonitorProject || document.getElementById('monitorProjectSel').value;
    if (!pid) return;
    const sequence = ++monitorRefreshSequence;
    const container = document.getElementById('monitorContent');
    try {
        const [latest, history, machineResult] = await Promise.all([
            API.get(`/api/projects/${pid}/monitoring/latest`),
            API.get(`/api/projects/${pid}/monitoring?hours=24&limit=200`),
            String(monitorMachineCache.projectId) === String(pid)
                ? Promise.resolve({machines: monitorMachineCache.machines})
                : API.get(`/api/projects/${pid}/machines`),
        ]);
        if (sequence !== monitorRefreshSequence || String(pid) !== String(state.selectedMonitorProject || document.getElementById('monitorProjectSel').value)) return;
        if (latest.status !== '成功' || history.status !== '成功') throw new Error(latest.msg || history.msg || '监测数据加载失败');
        monitorMachineCache = {projectId: pid, machines: machineResult.machines || []};
        MonitorView.render(container, latest.data, history.data || [], monitorMachineCache.machines, {projectId:pid, machineSnapshots:latest.machine_latest || [], projectCumulative:latest.project_cumulative});
    } catch (error) {
        if (sequence !== monitorRefreshSequence) return;
        // 自动刷新失败时保留上次读数，并明确标注，避免将失败冒充为无数据。
        const oldNotice = container.querySelector('.monitor-load-error');
        if (oldNotice) oldNotice.remove();
        const notice = document.createElement('p');
        notice.className = 'monitor-load-error';
        notice.textContent = `刷新失败：${error.message || '请稍后重试'}。已有读数为上次加载结果。`;
        container.prepend(notice);
    }
}

// ═══════════════════════════════════════════════════════════
// 报告中心（增强版：占位符参考 + 模板管理 + 生成报告）
// ═══════════════════════════════════════════════════════════

// 报告中心一次只选择一个项目。项目列表里的“生成报告”也会同步到这里。
function fillReportProjectSel() {
    const sel = document.getElementById('reportProjectSel');
    if (!sel) return;
    const previous = sel.value;
    sel.innerHTML = '<option value="">-- 选择项目 --</option>';
    (state.projects || []).filter(p => p.lifecycle_status === 'active').forEach(p => {
        sel.innerHTML += `<option value="${p.id}" data-search-text="${_esc(p.project_name + " " + (p.project_code || ""))}">${_esc(p.project_name)}</option>`;
    });
    if ((state.projects || []).some(p => String(p.id) === previous)) sel.value = previous;
    CustomSelect.refresh(sel);
    updateReportGate();
    refreshProjectPresetHeader();
}

// 日期两个框的含义随报告类型变：
// 核查报告 = 本核算期（这段期间的监测数据用于核算）；PDD/审定 = 仅作数据统计区间，
// 项目的计入期 A~B 取自项目基准日期，预估值按计入期首年段折算，与这里填的区间无关。
function updateReportDateLabels() {
    const type = (document.getElementById('reportTypeSel') || {}).value || '';
    const sLbl = document.getElementById('reportStartLabel');
    const eLbl = document.getElementById('reportEndLabel');
    const hint = document.getElementById('reportDateHint');
    if (!sLbl || !eLbl || !hint) return;
    if (type === 'verification') {
        sLbl.textContent = '本核算期起始';
        eLbl.textContent = '本核算期截止';
        hint.textContent = '这段时间的监测数据用于核算本期减排量（含期内总减排量、日均等）。';
    } else {
        sLbl.textContent = '数据统计区间起始';
        eLbl.textContent = '数据统计区间截止';
        hint.textContent = '仅决定报告里用哪段监测数据；项目的计入期取自项目基准日期，'
            + '预估值（封面＋表B.2/B.3/B.4）按「计入期首年段折算」，与这里填的区间无关。';
    }
}

// 刷新预设信息管理右侧副标题（项目名称）
function refreshProjectPresetHeader() {
    const nameEl = document.getElementById('projectPresetProjectName');
    if (!nameEl) return;
    const pid = projectPresetState.projectId ||
        parseInt((document.getElementById('reportProjectSel') || {}).value) || null;
    if (!pid) { nameEl.textContent = '（请先在生成报告区选择项目）'; return; }
    const p = state.projects.find(x => x.id === pid);
    nameEl.textContent = p ? '（' + p.project_name + '）' : '';
}

async function loadReportsTab() {
    // 填充项目选择器（始终刷新，保留当前选中，修复改项目名后不同步）
    fillReportProjectSel();
    // ★ 联动：选择项目后自动加载该项目预设
    const sel = document.getElementById('reportProjectSel');
    if (sel && !sel.dataset.presetBound) {
        sel.dataset.presetBound = '1';
        sel.addEventListener('change', function() {
            const pid = parseInt(this.value);
            if (pid) loadProjectPresets(pid);
            else loadProjectPresets(null);
        });
    }

    // 默认日期范围：上一年全年（按年度核算，1月1日 ~ 12月31日）
    const lastYear = new Date().getFullYear() - 1;
    document.getElementById('reportStartDate').value = lastYear + '-01-01';
    document.getElementById('reportEndDate').value = lastYear + '-12-31';

    // ★ 加载报告类型与模板
    loadReportTypes();
    // 加载可用字段（项目自有 + 自定义）
    loadVariableReference();
    // 加载预设（企业 + 全项目通用）
    loadPresets();
    loadGlobalPresets();
    // 加载历史
    loadReportHistory();
    updateReportDateLabels();
    // 开发者模式（影响阶段门禁按钮状态）
    try { const cfg = await API.get('/api/config'); state.devMode = !!cfg.dev_mode; } catch (e) {}
    updateReportGate();
}

// ★ 加载报告类型（固定两类）
async function loadReportTypes() {
    const sel = document.getElementById('reportTypeSel');
    if (!sel) return;

    try {
        const data = await API.get('/api/reports/formats');
        if (data.status !== '成功' || !data.formats.length) {
            // 回退：显示默认选项
            sel.innerHTML = `
                <option value="verification">核查报告</option>
                <option value="pdd">项目设计文档（PDD）</option>`;
        } else {
            sel.innerHTML = '';
            data.formats.forEach(f => {
                sel.innerHTML += `<option value="${f.key}">${f.label}</option>`;
            });
        }
    } catch (e) {
        // 加载失败时保留默认选项
    }
    CustomSelect.refresh(sel);
    toggleReportDateRange();
    loadTemplates();
    loadTenYearTotal();
}

// ★ 加载指定报告类型下的模板列表
async function loadTemplates() {
    const sel = document.getElementById('reportTemplateSel');
    const countEl = document.getElementById('reportTemplateCount');
    if (!sel) return;
    const type = document.getElementById('reportTypeSel').value;
    if (!type) {
        sel.innerHTML = '<option value="">-- 请先选择报告类型 --</option>';
        CustomSelect.refresh(sel);
        if (countEl) countEl.textContent = '';
        return;
    }
    currentReportType = type;
    try {
        const data = await API.get('/api/reports/templates?report_type=' + encodeURIComponent(type));
        const templates = (data.status === '成功' && data.templates) ? data.templates : [];
        if (!templates.length) {
            sel.innerHTML = '<option value="">-- 暂无模板 --</option>';
            if (countEl) countEl.textContent = '';
        } else {
            sel.innerHTML = '';
            templates.forEach(t => {
                sel.innerHTML += `<option value="${_esc(t.rel_path)}">${_esc(t.label)}</option>`;
            });
            if (countEl) countEl.textContent = `（${templates.length} 个）`;
        }
    } catch (e) {
        sel.innerHTML = '<option value="">-- 加载失败 --</option>';
    }
    CustomSelect.refresh(sel);
    // ★ 异步识别该类型下全部模板的字段（md5 缓存），完成后自动高亮默认模板
    preloadTemplateKeys(type, () => {
        if (sel.value) loadTemplateKeys(sel.value);
    });
}

// ── 异步识别某报告类型下全部模板字段（md5 缓存，只在 md5 变化时重新解析）──
async function preloadTemplateKeys(reportType, onDone) {
    try {
        const data = await API.get('/api/reports/template-keys?all=1&report_type=' + encodeURIComponent(reportType));
        if (data.status === '成功' && data.templates) {
            templateKeysCache = {};
            for (const [rel, info] of Object.entries(data.templates)) {
                templateKeysCache[rel] = { md5: info.md5 || '', keys: info.keys || {} };
            }
        }
    } catch (e) { /* 识别失败不阻塞 */ }
    if (onDone) onDone();
}

// PDD 也需要选择统计日期区间（用于「实际已产生的减排量」），不再按类型隐藏
function toggleReportDateRange() {
    const range = document.getElementById('reportDateRange');
    if (range) range.style.display = '';
}

// ── 选模板后高亮（优先用缓存；缓存 miss 或 md5 变化才请求）──
async function loadTemplateKeys(template) {
    showTemplateKeysLoading(true);
    templateRequiredKeys = { enterprise: new Set(), project: new Set(), other: new Set() };
    if (template) {
        const cached = templateKeysCache[template];
        if (cached && cached.keys) {
            // 命中缓存直接用
            templateRequiredKeys.enterprise = new Set(cached.keys.preset || []);
            templateRequiredKeys.project = new Set(cached.keys.project_preset || []);
            templateRequiredKeys.other = new Set(cached.keys.other || []);
        } else {
            // 缓存 miss：单独请求一次
            try {
                const data = await API.get('/api/reports/template-keys?template=' + encodeURIComponent(template));
                if (data.status === '成功' && data.keys) {
                    templateRequiredKeys.enterprise = new Set(data.keys.preset || []);
                    templateRequiredKeys.project = new Set(data.keys.project_preset || []);
                    templateRequiredKeys.other = new Set(data.keys.other || []);
                    templateKeysCache[template] = { md5: data.md5 || '', keys: data.keys };
                }
            } catch (e) { /* 识别失败不阻塞 */ }
        }
    }
    // 重渲染三区预设列表（高亮模板用到的字段）
    renderPresetList();
    renderGlobalPresetList();
    if (projectPresetState.projectId) renderProjectPresetList();
    // 重渲染「所有对象及其值」面板（高亮模板用到的参数）
    refreshAllObjectsHighlight();
    showTemplateKeysLoading(false);
}

function showTemplateKeysLoading(loading) {
    const el = document.getElementById('templateKeysHint');
    if (!el) return;
    const cnt = templateRequiredKeys.enterprise.size + templateRequiredKeys.project.size + templateRequiredKeys.other.size;
    if (loading) {
        el.textContent = '⏳ 正在识别模板字段...';
        el.style.color = 'var(--text-gray)';
    } else if (cnt > 0) {
        el.textContent = `（模板用到 ${cnt} 个字段，已在下方高亮，未填的标红）`;
        el.style.color = 'var(--warning)';
    } else {
        el.textContent = '';
    }
}

// 高亮「所有对象及其值」面板中模板用到的参数
function refreshAllObjectsHighlight() {
    const container = document.getElementById('allObjectsRef');
    if (!container) return;
    container.querySelectorAll('.placeholder-item').forEach(el => {
        const path = el.getAttribute('data-path') || '';
        // 模板里 {{ xxx }} 裸变量 / {{ xxx.yyy }} 路径，取最后一段或全路径匹配
        let highlighted = false;
        templateRequiredKeys.other.forEach(k => {
            if (path === k || path.endsWith('.' + k) || path === k.split('.')[0]) {
                highlighted = true;
            }
        });
        // 也匹配 project_preset.xxx / preset.xxx 前缀（这些在预设区高亮，这里也标记）
        templateRequiredKeys.project.forEach(k => {
            if (path === 'project_preset.' + k) highlighted = true;
        });
        templateRequiredKeys.enterprise.forEach(k => {
            if (path === 'preset.' + k) highlighted = true;
        });
        el.classList.toggle('template-used', highlighted);
    });
}

/* ═══════════════════════════════════════════════════════════
   批量生成报告（多选项目 → 逐项目生成 → 打包 zip 下载）
   ═══════════════════════════════════════════════════════════ */

// 单份 / 批量统一走 generateReport()：
// 勾选 1 个项目 → 单份接口，下 .docx；勾选 ≥2 个 → 批量接口，打包下 zip。
// 原来那套 initBatchGenCard / loadBatchTemplates / startBatchGenerate 已合并进去。

/* ═══════════════════════════════════════════════════════════
   数据导入（历史监测数据补录）
   ═══════════════════════════════════════════════════════════ */

function initImportTab() {
    const sel = document.getElementById('importProjectSel');
    if (sel && sel.options.length <= 1 && state.projects.length) {
        state.projects.forEach(p => {
            sel.innerHTML += `<option value="${p.id}" data-search-text="${_esc(p.project_name + " " + (p.project_code || ""))}">${_esc(p.project_name)}</option>`;
        });
        CustomSelect.refresh(sel);
    }
    if (sel && !sel._importBound) {
        sel._importBound = true;
        sel.addEventListener('change', () => {
            if (sel.value) {
                const pid = parseInt(sel.value);
                loadImportMeta(pid);
                // 导入数据也归属到机器上：换项目就把机器下拉跟着换掉
                fillMachineSel(document.getElementById('importMachineSel'), pid, '-- 选择机器 --', true);
            } else {
                resetImportMeta();
                fillMachineSel(document.getElementById('importMachineSel'), '', '-- 选择机器 --', false);
            }
        });
    }
    // 再次切回导入页时已选着项目，机器下拉得补上（否则空着）
    if (sel && sel.value && document.getElementById('importMachineSel').options.length <= 1) {
        fillMachineSel(document.getElementById('importMachineSel'), parseInt(sel.value), '-- 选择机器 --', true);
    }
}

// 从机器行的「导入」按钮打开，直接锁定项目（和可选机器）
async function openImportModal(pid, mid) {
    if (!state.projects.length) {
        try { const d = await API.get('/api/projects'); state.projects = d.projects || []; } catch(e) {}
    }
    initImportTab();
    const sel = document.getElementById('importProjectSel');
    if (pid) {
        sel.value = String(pid);
        CustomSelect.refresh(sel);
        loadImportMeta(pid);
        await fillMachineSel(document.getElementById('importMachineSel'), pid, '-- 选择机器 --', false);
        if (mid) {
            const msel = document.getElementById('importMachineSel');
            msel.value = String(mid);
            CustomSelect.refresh(msel);
        }
    }
    // 清掉上次的残留状态
    const st = document.getElementById('importStatus');
    if (st) { st.classList.add('hidden'); st.textContent = ''; }
    const fi = document.getElementById('importFileInput');
    if (fi) fi.value = '';
    const fn = document.getElementById('importFileName');
    if (fn) fn.textContent = '未选择文件';
    document.getElementById('importModal').classList.remove('hidden', 'closing');
}

function closeImportModal() {
    const modal = document.getElementById('importModal');
    if (!modal) return;
    modal.classList.add('hidden');
}

function resetImportMeta() {
    const hintEl = document.getElementById('importFieldHint');
    if (hintEl) hintEl.textContent = '';
    const epEl = document.getElementById('importApiEndpoint');
    if (epEl) epEl.textContent = '请先选择项目';
    const exEl = document.getElementById('importApiExample');
    if (exEl) exEl.textContent = '请先选择项目';
}

async function loadImportMeta(pid) {
    const hintEl = document.getElementById('importFieldHint');
    const epEl = document.getElementById('importApiEndpoint');
    const exEl = document.getElementById('importApiExample');
    if (hintEl) hintEl.textContent = '加载项目公式字段…';
    try {
        const meta = await API.get(`/api/projects/${pid}/monitoring/import-meta`);
        if (meta.status !== '成功') {
            if (hintEl) hintEl.textContent = meta.msg || '无法获取公式字段';
            return;
        }
        const fields = meta.raw_fields || [];
        const labels = meta.labels || {};
        const units = meta.units || {};
        // 单位跟列名一起显示 —— 导入历史数据最容易填错的就是 kWh/MWh、m³/L
        const cols = ['时间', ...fields.map(f => units[f] ? `${labels[f] || f}[${units[f]}]` : (labels[f] || f)),
                      units.carbon_reduction ? `碳减排量(可选)[${units.carbon_reduction}]` : '碳减排量(可选)'];
        if (hintEl) hintEl.textContent = '模板列（方括号内是单位）：' + cols.join('、')
            + '　｜　碳减排量那列：留空 = 按读数用公式算；填了 = 直接采用你填的值（公式不再参与）';
        const endpoint = meta.endpoint || `/api/projects/${pid}/monitoring/ingest`;
        if (epEl) epEl.textContent = window.location.origin + endpoint;
        const sample = { timestamp: '2023-01-31 23:59:59' };
        fields.forEach(f => { sample[f] = 0; });
        if (exEl) exEl.textContent = 'POST ' + endpoint + '\n' + JSON.stringify({ rows: [sample] }, null, 2);
    } catch (e) {
        if (hintEl) hintEl.textContent = '加载失败: ' + e.message;
    }
}

// 简易 CSV 解析（支持引号转义、逗号、换行、BOM）
function downloadImportTemplate(format) {
    const pid = document.getElementById('importProjectSel').value;
    if (!pid) { alert('请先选择导入到哪个项目'); return; }
    const a = document.createElement('a');
    a.href = `/api/projects/${pid}/monitoring/import-template?format=${format || 'csv'}`;
    a.download = format === 'xlsx' ? '历史数据导入模板.xlsx' : '历史数据导入模板.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
}

async function startImport() {
    const pid = parseInt(document.getElementById('importProjectSel').value);
    const statusEl = document.getElementById('importStatus');
    const fileInput = document.getElementById('importFileInput');
    if (!pid) { alert('请先选择导入到哪个项目'); return; }
    const file = fileInput.files[0];
    if (!file) { alert('请先选择数据文件（.csv / .xlsx）'); return; }
    if (!confirm(`即将向项目 #${pid} 导入文件 ${file.name}，确认继续？`)) return;

    statusEl.className = 'status-msg';
    statusEl.textContent = '正在上传并导入…';
    statusEl.classList.remove('hidden');

    const fd = new FormData();
    fd.append('file', file);
    // 选到机器才带上，没选就让后端落到项目第一台
    const mid = document.getElementById('importMachineSel').value;
    if (mid) fd.append('machine_id', mid);
    try {
        const resp = await fetch(`/api/projects/${pid}/monitoring/import-file`, { method: 'POST', body: fd });
        const result = await resp.json();
        if (result.status === '成功') {
            let msg = `✅ 导入完成：新增 ${result.imported} 条，跳过重复 ${result.skipped} 条`;
            if (result.errors && result.errors.length) {
                msg += `；${result.errors.length} 行无效（如 ${result.errors[0]}）`;
            }
            statusEl.className = 'status-msg success';
            statusEl.textContent = msg;
            fileInput.value = '';
            document.getElementById('importFileName').textContent = '未选择文件';
        } else {
            statusEl.className = 'status-msg error';
            statusEl.textContent = '导入失败: ' + (result.msg || '未知错误') +
                (result.errors && result.errors.length ? '（' + result.errors[0] + ' 等）' : '');
        }
    } catch (e) {
        statusEl.className = 'status-msg error';
        statusEl.textContent = '导入失败: ' + e.message;
    }
}

// ── 网页内 Python 控制台（调试变量用）──
async function runConsoleCode() {
    const input = document.getElementById('consoleInput');
    const output = document.getElementById('consoleOutput');
    const code = input ? input.value.trim() : '';
    if (!code) { if (output) { output.textContent = '请输入代码'; output.classList.remove('hidden'); } return; }
    const pid = projectPresetState.projectId ||
        parseInt((document.getElementById('reportProjectSel') || {}).value) || null;

    output.classList.remove('hidden');
    output.className = 'console-output';
    output.textContent = '⏳ 运行中...';
    try {
        const resp = await fetch('/api/reports/eval-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, project_id: pid }),
        });
        const data = await resp.json();
        if (data.status !== '成功') {
            output.textContent = '❌ ' + (data.msg || '运行失败');
            output.classList.add('err');
            return;
        }
        if (data.error) {
            output.textContent = '❌ ' + data.error;
            output.classList.add('err');
        } else {
            output.textContent = data.result;
            output.classList.add('ok');
        }
        // 类型提示追加
        if (data.type && data.type !== 'Error') {
            output.textContent += '\n（类型：' + data.type + '）';
        }
    } catch (e) {
        output.textContent = '❌ 网络错误: ' + e.message;
        output.classList.add('err');
    }
}

// 更新控制台「当前项目」提示
function updateConsoleProjectHint() {
    const el = document.getElementById('consoleProjectHint');
    if (!el) return;
    const pid = projectPresetState.projectId ||
        parseInt((document.getElementById('reportProjectSel') || {}).value) || null;
    if (!pid) { el.textContent = '未选择'; return; }
    const p = state.projects.find(x => x.id === pid);
    el.textContent = p ? p.project_name : ('项目#' + pid);
}

// ── 全局 Toast 提示 ──
let _toastTimer = null;
function showToast(msg) {
    let toast = document.getElementById('globalToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'globalToast';
        toast.className = 'global-toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add('show');
    if (_toastTimer) clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => toast.classList.remove('show'), 1600);
}

// ── 点击字段模块复制字段路径到剪贴板 ──
async function copyVarPath(el) {
    const text = el.getAttribute('data-path') || '';
    if (!text) return;
    let ok = false;
    try {
        await navigator.clipboard.writeText(text);
        ok = true;
    } catch (e) {
        // 回退：兼容非安全上下文
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            ok = document.execCommand('copy');
            ta.remove();
        } catch (_) { ok = false; }
    }
    showToast(ok ? '✅ 已复制 ' + text : '复制失败，请手动复制');
    if (ok) {
        el.classList.add('copied');
        setTimeout(() => el.classList.remove('copied'), 600);
    }
}

// ── 导出全部可用字段为 Word 文档 ──
function exportVariableReference() {
    const pid = projectPresetState.projectId ||
        parseInt((document.getElementById('reportProjectSel') || {}).value) || '';
    let url = '/api/reports/variables/export';
    if (pid) url += '?project_id=' + encodeURIComponent(pid);
    window.open(url, '_blank');
}

// ── Jinja2 可用字段面板 ──

// 子标签切换
function switchVarTab(tabName) {
    document.querySelectorAll('.var-tab-btn').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`[data-var-tab="${tabName}"]`);
    if (btn) btn.classList.add('active');
    document.querySelectorAll('.var-tab-content').forEach(c => c.classList.add('hidden'));
    const panel = document.getElementById('varTab' + tabName.charAt(0).toUpperCase() + tabName.slice(1));
    if (panel) panel.classList.remove('hidden');
}

// 加载可用字段（项目自有字段 + 自定义字段）
async function loadVariableReference(projectId) {
    const allObjectsContainer = document.getElementById('allObjectsRef');

    let url = '/api/reports/variables';
    if (projectId) url += '?project_id=' + encodeURIComponent(projectId);

    try {
        const data = await API.get(url);
        if (data.status !== '成功') {
            if (allObjectsContainer) allObjectsContainer.innerHTML = '<p class="muted">加载对象清单失败</p>';
            return;
        }
        renderAllObjects(data.all_objects || []);
        updateConsoleProjectHint();
    } catch(e) {
        if (allObjectsContainer) allObjectsContainer.innerHTML = '<p class="muted">加载失败: ' + e.message + '</p>';
    }
}

// 渲染「项目自有字段」面板
function renderProjectFields(sections) {
    const container = document.getElementById('projectFieldsRef');
    if (!container) return;
    if (!sections.length) {
        container.innerHTML = '<p class="muted">选择项目后显示该项目自有字段</p>';
        return;
    }

    let html = '';
    sections.forEach(sec => {
        html += `<div class="placeholder-category" data-category="${sec.key}">
            <h4>${_esc(sec.label)} (${sec.variables.length})</h4>
            <div class="placeholder-grid">`;
        sec.variables.forEach(v => {
            const typeClass = v.type === 'control' ? 'ph-type-control' : (v.type === 'number' ? 'ph-type-number' : '');
            html += `<div class="placeholder-item" data-path="${_esc(v.path)}" title="点击复制" onclick="App.copyVarPath(this)" data-search-text="${_esc(v.path)} ${_esc(v.label)} ${_esc(v.value || '')}">
                <code class="ph-path">${_esc(v.path)}</code>
                <span class="placeholder-label">${_esc(v.label)}</span>
                ${v.value !== undefined && v.value !== '' ? `<div class="ph-example" title="当前值">${_esc(v.value)}</div>` : ''}
                ${typeClass ? `<span class="ph-type-tag ${typeClass}">${v.type}</span>` : ''}
            </div>`;
        });
        html += '</div></div>';
    });
    container.innerHTML = html;
}

// 渲染「自定义字段」面板（来自企业预设 + 项目预设，有值才可用）
function renderCustomFields(sections) {
    const container = document.getElementById('customFieldsRef');
    if (!container) return;
    if (!sections.length) {
        container.innerHTML = '<p class="muted">暂无自定义字段</p>';
        return;
    }

    let html = '';
    sections.forEach(sec => {
        const vars = sec.variables || [];
        html += `<div class="placeholder-category" data-category="${sec.key}">
            <h4>${_esc(sec.label)} (${vars.length})</h4>
            <div class="placeholder-grid">`;
        vars.forEach(v => {
            const avail = v.available;
            html += `<div class="placeholder-item ${avail ? '' : 'ph-unavailable'}" data-path="${_esc(v.path)}" title="点击复制" onclick="App.copyVarPath(this)" data-search-text="${_esc(v.path)} ${_esc(v.label)} ${_esc(v.value || '')}">
                <code class="ph-path">${_esc(v.path)}</code>
                <span class="placeholder-label">${_esc(v.label)}</span>
                ${avail
                    ? `<div class="ph-example" title="当前值">${_esc(v.value)}</div>`
                    : `<div class="ph-example ph-unavail-tag">未填写，不可用</div>`}
            </div>`;
        });
        html += '</div></div>';
    });
    container.innerHTML = html;
}

// 渲染「所有对象及其值」面板（context 全部顶层对象 → 变量 → 当前值）
function renderAllObjects(groups) {
    const container = document.getElementById('allObjectsRef');
    if (!container) return;
    if (!groups || !groups.length) {
        container.innerHTML = '<p class="muted">选择项目后显示全部对象、变量及当前值</p>';
        return;
    }

    let html = '<p class="muted" style="margin-bottom:10px">'
        + '模板中用 <code>{{ 字段路径 }}</code> 引用；带 <span class="ph-type-tag ph-type-computable">可计算</span> '
        + '标记的变量可在「自动计算」类型预设的表达式中直接使用（支持 <code>+ - * / ( )</code>）。点击任意条目复制。</p>';
    groups.forEach(sec => {
        const vars = sec.vars || [];
        html += `<div class="placeholder-category" data-category="obj_${_esc(sec.key)}">
            <h4>${_esc(sec.label)} (${vars.length})</h4>
            ${sec.hint ? `<div class="placeholder-desc" style="margin:-4px 0 6px">${_esc(sec.hint)}</div>` : ''}
            <div class="placeholder-grid">`;
        vars.forEach(v => {
            html += `<div class="placeholder-item" data-path="${_esc(v.path)}" title="点击复制" onclick="App.copyVarPath(this)" data-search-text="${_esc(v.path)} ${_esc(v.label)} ${_esc(v.value || '')}">
                <code class="ph-path">${_esc(v.path)}</code>
                <span class="placeholder-label">${_esc(v.label)}</span>
                ${v.value !== undefined && v.value !== '' ? `<div class="ph-example" title="当前值">${_esc(v.value)}</div>` : ''}
                ${v.computable ? '<span class="ph-type-tag ph-type-computable">可计算</span>' : ''}
            </div>`;
        });
        html += '</div></div>';
    });
    container.innerHTML = html;
}

function renderControlStructures(controls) {
    const container = document.getElementById('controlReference');
    if (!container) return;
    if (!controls.length) {
        container.innerHTML = '<p class="muted">暂无控制结构参考</p>';
        return;
    }

    let html = '';
    controls.forEach(ctrl => {
        html += `<div class="placeholder-item control-item" data-path="${_esc(ctrl.syntax)}" title="点击复制" onclick="App.copyVarPath(this)" data-search-text="${_esc(ctrl.syntax)} ${_esc(ctrl.label)}">
            <code class="ph-path">${_esc(ctrl.syntax)}</code>
            <span class="placeholder-label">${_esc(ctrl.label)}</span>
            ${ctrl.description ? `<div class="placeholder-desc">${_esc(ctrl.description)}</div>` : ''}
        </div>`;
    });
    container.innerHTML = html;
}

// ── 企业预设信息管理 ──

let presetState = {
    presets: [],
    categories: [],
    activeKey: null,
    expandedCats: new Set(),   // 已展开的分类（重渲染后恢复）
};

// ── 项目预设状态 ──
let projectPresetState = {
    projectId: null,
    presets: [],
    categories: [],
    activeKey: null,
    expandedCats: new Set(),
};

// ── 全项目通用预设状态（值对所有项目相同，独立于项目选择）──
let globalPresetState = {
    presets: [],
    categories: [],
    activeKey: null,
    expandedCats: new Set(),
};

// ── 模板用到的预设 key（选模板后自动识别，用于预设管理高亮）──
// enterprise: 模板里 {{ preset.xxx }} 的 key；project: {{ project_preset.xxx }} 的 key；
// other: 其它裸变量/对象参数（如 monitoring.total_reduction_fmt）
let templateRequiredKeys = { enterprise: new Set(), project: new Set(), other: new Set() };
// 模板字段识别缓存：{ rel_path: { md5, keys:{preset:[],project_preset:[],other:[]} } }
let templateKeysCache = {};
// 当前报告类型
let currentReportType = '';

// ── 内联编辑器状态：编辑框插入到被点击预设块的正下方 ──
let inlineEditor = { zone: null, key: null };
// 展开时被上移的预设块原位置（用于取消后回归原位）
let expandedItemRef = null;

// ── 未保存草稿暂存：{ enterprise: {key: value}, project: {key: value}, global: {key: value} } ──
// 切换到其他预设块时自动暂存，切回时恢复；保存成功后清除
let presetDrafts = { enterprise: {}, project: {}, global: {} };

async function loadPresets() {
    const container = document.getElementById('presetList');
    if (!container) return;
    container.innerHTML = '<p class="muted">加载中...</p>';

    try {
        const data = await API.get('/api/presets');
        if (data.status !== '成功') {
            container.innerHTML = '<p class="muted">加载预设失败</p>';
            return;
        }
        presetState.presets = data.presets || [];
        presetState.categories = data.categories || [];
        renderPresetList();
    } catch(e) {
        container.innerHTML = '<p class="muted">加载失败: ' + e.message + '</p>';
    }
}


function renderPresetList() {
    const container = document.getElementById('presetList');
    if (!container) return;

    let presets = presetState.presets;

    // 按分类分组（可折叠模块，空分类也显示骨架）
    const grouped = {};
    presets.forEach(p => {
        const cat = p.category || '自定义';
        if (!grouped[cat]) grouped[cat] = [];
        grouped[cat].push(p);
    });

    let catsToRender = getOrderedCategories('enterprise');
    if (!catsToRender.length) {
        container.innerHTML = '<p class="muted">暂无预设，点击「+ 新建」创建</p>';
        return;
    }

    let html = '';
    for (const cat of catsToRender) {
        const items = grouped[cat] || [];
        const isOpen = presetState.expandedCats.has(cat);
        html += `<div class="preset-cat-group">
            <div class="preset-cat-label" draggable="true" onclick="App.togglePresetCat(this)"
                ondragstart="App.catDragStart(event,'enterprise','${_esc(cat)}')" ondragend="App.catDragEnd(event)"
                ondragover="App.presetCatDragOver(event)" ondragleave="App.presetCatDragLeave(event)"
                ondrop="App.presetCatDrop(event,'enterprise','${_esc(cat)}')">
                <span class="preset-cat-arrow">${isOpen ? '▾' : '▸'}</span>${_esc(cat)}
                <span class="preset-cat-count">(${items.length})</span>
            </div>
            <div class="preset-cat-body${isOpen ? '' : ' collapsed'}"
                ondragover="App.presetCatDragOver(event)" ondragleave="App.presetCatDragLeave(event)"
                ondrop="App.presetCatDrop(event,'enterprise','${_esc(cat)}')">`;
        if (!items.length) {
            html += '<div class="preset-cat-empty">暂无字段，可拖拽字段到此分类</div>';
        }
        items.forEach(p => {
            const activeClass = (presetState.activeKey === p.key) ? ' active' : '';
            const typeIcon = {text:'📝',richtext:'📄',image:'🖼',code:'🐍'}[p.type] || '📝';
            const hasDraft = presetDrafts.enterprise[p.key] !== undefined;
            const isPack = !!p._pack;
            // 模板用到的字段高亮；未填值标红
            const isRequired = templateRequiredKeys.enterprise.has(p.key);
            const isMissing = isRequired && !((p.value || '').trim());
            const reqClass = (isRequired ? ' template-required' : '') + (isMissing ? ' template-missing' : '');
            const onClick = isPack
                ? `onclick="App.showToast('外载预设只读，请修改 presets_packs/${_esc(p._pack)}.py')"`
                : `onclick="App.selectPreset('${_esc(p.key)}')"`;
            html += `<div class="preset-list-item${activeClass}${reqClass}" draggable="${!isPack}" data-key="${_esc(p.key)}" data-type="${_esc(p.type || 'text')}" data-zone="enterprise"
                ${onClick}
                ondragstart="App.presetDragStart(event,'enterprise','${_esc(p.key)}')"
                ondragend="App.presetDragEnd(event)"
                ondragover="App.presetItemDragOver(event)" ondragleave="App.presetItemDragLeave(event)"
                ondrop="App.presetItemDrop(event,'enterprise','${_esc(p.key)}')">
                <span class="preset-type-icon">${typeIcon}</span>
                <span class="preset-item-label" title="${_esc(p.label)}">${_esc(p.label)}</span>
                ${isPack ? '<span class="preset-pack-tag" title="来自企业预设外载包，只读">外载</span>' : ''}
                ${hasDraft ? '<span class="preset-draft-dot" title="有未保存的修改"></span>' : ''}
                ${isPack ? '' : `<button class="preset-edit-btn" title="编辑预设参数" onclick="event.stopPropagation();App.openPresetModal('${_esc(p.key)}')">✎</button>`}
            </div>`;
        });
        html += '</div></div>';
    }
    container.innerHTML = html;

    // 重渲染后若编辑器处于打开状态，重新挂载到对应预设块下方
    if (inlineEditor.zone === 'enterprise' && inlineEditor.key) {
        attachInlineEditor('enterprise', inlineEditor.key, false);
    }
}

function selectPreset(key) {
    selectPresetInline('enterprise', key);
}

// ── 内联编辑器核心逻辑（企业/项目预设共用）──

function getPresetStore(zone) {
    if (zone === 'project') return projectPresetState;
    if (zone === 'global') return globalPresetState;
    return presetState;
}

// ── 分类顺序：后端定义顺序 + 数据中出现的分类，再按 localStorage 自定义顺序覆盖 ──
function getOrderedCategories(zone) {
    const st = getPresetStore(zone);
    const seen = [];
    (st.categories || []).forEach(c => { if (!seen.includes(c)) seen.push(c); });
    st.presets.forEach(p => {
        const c = p.category || '自定义';
        if (!seen.includes(c)) seen.push(c);
    });
    try {
        const saved = JSON.parse(localStorage.getItem('presetCatOrder_' + zone) || '[]');
        if (Array.isArray(saved) && saved.length) {
            const valid = saved.filter(c => seen.includes(c));
            const rest = seen.filter(c => !valid.includes(c));
            return valid.concat(rest);
        }
    } catch (_) {}
    return seen;
}

function saveCatOrder(zone, orderedCats) {
    try {
        localStorage.setItem('presetCatOrder_' + zone, JSON.stringify(orderedCats));
    } catch (_) {}
}

// 点击预设块：在其正下方展开编辑框；再次点击同一块则暂存并收起
function selectPresetInline(zone, key) {
    const st = getPresetStore(zone);

    // 点击已打开的同一块 → 暂存草稿并收起
    if (inlineEditor.zone === zone && inlineEditor.key === key) {
        stashEditorDraft();
        closeInlineEditor(true);
        st.activeKey = null;
        refreshPresetHighlight(zone);
        return;
    }

    // 切换前，暂存当前正在编辑的内容（问题4：未保存不丢失）
    if (inlineEditor.key) stashEditorDraft();

    st.activeKey = key;
    inlineEditor.zone = zone;
    inlineEditor.key = key;
    refreshPresetHighlight(zone);
    attachInlineEditor(zone, key, true);
}

function refreshPresetHighlight(zone) {
    const st = getPresetStore(zone);
    const listId = zone === 'project' ? 'projectPresetList'
        : (zone === 'global' ? 'globalPresetList' : 'presetList');
    document.querySelectorAll('#' + listId + ' .preset-list-item').forEach(el => {
        el.classList.toggle('active', el.getAttribute('data-key') === st.activeKey);
    });
}

// 把编辑器 DOM 挂到指定预设块的正下方（动画展开）
function attachInlineEditor(zone, key, animate) {
    removeInlineEditorDom();
    const listId = zone === 'project' ? 'projectPresetList'
        : (zone === 'global' ? 'globalPresetList' : 'presetList');
    let item = null;
    document.querySelectorAll('#' + listId + ' .preset-list-item').forEach(el => {
        if (el.getAttribute('data-key') === key) item = el;
    });
    if (!item) return;

    // 确保所在分类处于展开状态
    const body = item.closest('.preset-cat-body');
    if (body && body.classList.contains('collapsed')) {
        body.classList.remove('collapsed');
        const label = body.previousElementSibling;
        const arrow = label && label.querySelector('.preset-cat-arrow');
        if (arrow) arrow.textContent = '▾';
        const group = item.closest('.preset-cat-group');
        const catLabel = group && group.querySelector('.preset-cat-label');
        if (catLabel) {
            const st = getPresetStore(zone);
            const catName = catLabel.textContent.replace(/[▸▾]/, '').replace(/\(\d+\)/, '').trim();
            st.expandedCats.add(catName);
        }
    }

    // ★ 点击预设：上移到分类最前 + 占满整行展开；记录原位置供取消时回归
    if (body) {
        expandedItemRef = { item, next: item.nextSibling };
        body.insertBefore(item, body.firstChild);
    } else {
        expandedItemRef = null;
    }
    item.classList.add('expanded');
    document.querySelectorAll('#' + listId + ' .preset-list-item.expanded').forEach(el => {
        if (el !== item) el.classList.remove('expanded');
    });

    const editorId = zone === 'project' ? 'projectPresetEditorInline'
        : (zone === 'global' ? 'globalPresetEditorInline' : 'presetEditorInline');
    const wrap = document.createElement('div');
    wrap.className = 'preset-inline-editor';
    wrap.innerHTML = '<div class="preset-inline-editor-inner">' +
        '<div class="preset-editor inline" id="' + editorId + '"></div></div>';
    item.insertAdjacentElement('afterend', wrap);

    if (zone === 'project') renderProjectPresetEditor(key);
    else if (zone === 'global') renderGlobalPresetEditor(key);
    else renderPresetEditor(key);

    if (animate) {
        requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.add('open')));
    } else {
        wrap.classList.add('open');
    }
}

function removeInlineEditorDom() {
    document.querySelectorAll('.preset-inline-editor').forEach(el => el.remove());
    document.querySelectorAll('.preset-list-item.expanded').forEach(el => el.classList.remove('expanded'));
    // 恢复被上移的预设块到原位（取消展开时回归原位）
    if (expandedItemRef) {
        const { item, next } = expandedItemRef;
        expandedItemRef = null;
        if (item && item.parentNode) {
            if (next && next.parentNode === item.parentNode) {
                item.parentNode.insertBefore(item, next);
            } else {
                item.parentNode.appendChild(item);
            }
        }
    }
}

// 收起内联编辑器（animate=true 时播放收起动画）
function closeInlineEditor(animate) {
    inlineEditor.zone = null;
    inlineEditor.key = null;
    const wrap = document.querySelector('.preset-inline-editor');
    if (!wrap) { removeInlineEditorDom(); return; }
    if (!animate) { removeInlineEditorDom(); return; }
    wrap.classList.remove('open');
    let removed = false;
    const doRemove = () => { if (!removed) { removed = true; removeInlineEditorDom(); } };
    wrap.addEventListener('transitionend', doRemove, { once: true });
    setTimeout(doRemove, 400); // 兜底
}

// 读取当前打开的编辑器内容（用于暂存草稿）
function readOpenEditorValue() {
    if (!inlineEditor.zone || !inlineEditor.key) return undefined;
    const zone = inlineEditor.zone, key = inlineEditor.key;
    const st = getPresetStore(zone);
    const p = st.presets.find(x => x.key === key);
    if (!p || p.type === 'image') return undefined; // image 通过上传即时保存
    if (zone === 'project') {
        if (p.type === 'richtext') {
            const el = document.getElementById('ppRichtext_' + key);
            return el ? el.innerHTML : undefined;
        }
        if (p.type === 'code') {
            const el = document.getElementById('ppCode_' + key);
            return el ? el.value : undefined;
        }
        if (p.type === 'table') return collectTableValue('pp', key);
        const el = document.getElementById('ppVal_' + key);
        return el ? el.value : undefined;
    }
    if (zone === 'global') {
        if (p.type === 'richtext') {
            const el = document.getElementById('gpRichtext_' + key);
            return el ? el.innerHTML : undefined;
        }
        if (p.type === 'code') {
            const el = document.getElementById('gpCode_' + key);
            return el ? el.value : undefined;
        }
        if (p.type === 'table') return collectTableValue('gp', key);
        const el = document.getElementById('gpText_' + key) || document.getElementById('gpVal_' + key);
        return el ? el.value : undefined;
    }
    if (p.type === 'richtext') {
        const el = document.getElementById('richtextEditor_' + key);
        return el ? el.innerHTML : undefined;
    }
    if (p.type === 'code') {
        const el = document.getElementById('presetCode_' + key);
        return el ? el.value : undefined;
    }
    const el = document.getElementById('presetText_' + key);
    return el ? el.value : undefined;
}

// 暂存当前编辑器内容到草稿（仅在与已保存值不同时记录）
function stashEditorDraft() {
    if (!inlineEditor.zone || !inlineEditor.key) return;
    const zone = inlineEditor.zone, key = inlineEditor.key;
    const st = getPresetStore(zone);
    const p = st.presets.find(x => x.key === key);
    if (!p) return;
    const val = readOpenEditorValue();
    if (val === undefined) return;
    if (val !== (p.value || '')) {
        presetDrafts[zone][key] = val;
        // 立即在列表项上显示未保存标记
        const listId = zone === 'project' ? 'projectPresetList'
            : (zone === 'global' ? 'globalPresetList' : 'presetList');
        document.querySelectorAll('#' + listId + ' .preset-list-item').forEach(el => {
            if (el.getAttribute('data-key') === key && !el.querySelector('.preset-draft-dot')) {
                const dot = document.createElement('span');
                dot.className = 'preset-draft-dot';
                dot.title = '有未保存的修改';
                const codeEl = el.querySelector('.preset-item-key');
                el.insertBefore(dot, codeEl || null);
            }
        });
    } else {
        delete presetDrafts[zone][key];
    }
}

// 保存成功后：清草稿、清标记、提示已保存、刷新可用字段面板
function afterPresetSaved(zone, key) {
    delete presetDrafts[zone][key];
    const listId = zone === 'project' ? 'projectPresetList'
        : (zone === 'global' ? 'globalPresetList' : 'presetList');
    // global 和 project 共用 {{ project_preset.xxx }} 命名空间，都看 templateRequiredKeys.project
    const requiredSet = (zone === 'enterprise') ? templateRequiredKeys.enterprise : templateRequiredKeys.project;
    const stateList = zone === 'project' ? projectPresetState.presets
        : (zone === 'global' ? globalPresetState.presets : presetState.presets);
    const saved = stateList.find(p => p.key === key);
    const isRequired = requiredSet.has(key);
    const filled = !!((saved && saved.value) || '').trim();

    document.querySelectorAll('#' + listId + ' .preset-list-item').forEach(el => {
        if (el.getAttribute('data-key') !== key) return;
        const dot = el.querySelector('.preset-draft-dot');
        if (dot) dot.remove();
        // 「未填写」是渲染时按值算出来的类，只在整块重渲染时才更新。
        // 这里就地重算，否则填完保存后徽标还挂着「未填写」。
        el.classList.toggle('template-required', isRequired);
        el.classList.toggle('template-missing', isRequired && !filled);
    });
    // 编辑器内「已保存」提示
    const editorId = zone === 'project' ? 'projectPresetEditorInline'
        : (zone === 'global' ? 'globalPresetEditorInline' : 'presetEditorInline');
    const editor = document.getElementById(editorId);
    if (editor) {
        let hint = editor.querySelector('.preset-save-hint');
        if (!hint) {
            hint = document.createElement('span');
            hint.className = 'preset-save-hint';
            const footer = editor.querySelector('.preset-editor-footer');
            if (footer) footer.appendChild(hint);
            else editor.appendChild(hint);
        }
        hint.textContent = '✅ 已保存';
        hint.classList.remove('fading');
        setTimeout(() => hint.classList.add('fading'), 1200);
    }
    // 刷新右侧「可用字段」面板（问题3：保存后同步更新）
    loadVariableReference(projectPresetState.projectId || undefined);
}

function renderPresetEditor(key) {
    const container = document.getElementById('presetEditorInline');
    if (!container) return;

    const p = presetState.presets.find(x => x.key === key);
    if (!p) {
        closeInlineEditor(false);
        return;
    }

    // 草稿优先：未保存的修改切回来时恢复（问题4）
    const draft = presetDrafts.enterprise[key];
    const curValue = draft !== undefined ? draft : (p.value || '');
    const draftTag = draft !== undefined ? '<span class="tag tag-warn">未保存</span>' : '';

    let editorHtml = '';
    if (p.type === 'richtext') {
        editorHtml = `<div class="preset-editor-header">
            <span class="preset-editor-label">${_esc(p.label)} <code>preset.${_esc(p.key)}</code></span>${draftTag}
        </div>
        <div class="richtext-toolbar">
            <button onclick="App.richTextCmd('bold')" title="加粗"><b>B</b></button>
            <button onclick="App.richTextCmd('italic')" title="斜体"><i>I</i></button>
            <button onclick="App.richTextCmd('underline')" title="下划线"><u>U</u></button>
            <span class="tb-sep"></span>
            <button onclick="App.richTextCmd('formatBlock','h2')" title="标题2">H2</button>
            <button onclick="App.richTextCmd('formatBlock','h3')" title="标题3">H3</button>
            <button onclick="App.richTextCmd('formatBlock','p')" title="正文">P</button>
            <span class="tb-sep"></span>
            <button onclick="App.richTextCmd('insertUnorderedList')" title="无序列表">•</button>
            <button onclick="App.richTextCmd('insertOrderedList')" title="有序列表">1.</button>
            <span class="tb-sep"></span>
            <label class="rt-img-btn" title="插入图片">
                🖼 <input type="file" accept="image/*" style="display:none" onchange="App.richTextInsertImage(this,'${_esc(p.key)}')">
            </label>
        </div>
        <div class="richtext-editor" id="richtextEditor_${_esc(p.key)}" contenteditable="true">${curValue}</div>
        <input type="file" id="presetImageInput_${_esc(p.key)}" accept="image/*" style="display:none" onchange="App.presetImageUpload(this,'${_esc(p.key)}')">`;
    } else if (p.type === 'image') {
        editorHtml = `<div class="preset-editor-header">
            <span class="preset-editor-label">${_esc(p.label)} <code>preset.${_esc(p.key)}</code></span>
        </div>
        <div class="preset-image-area">
            ${p.value ? `<img src="/api/presets/images/${_esc(p.value.split(/[\\/]/).pop())}" alt="${_esc(p.label)}" class="preset-preview-img">` : '<div class="preset-image-placeholder">暂无图片</div>'}
            <div style="margin-top:8px">
                <label class="btn btn-sm btn-outline" style="cursor:pointer">
                    上传图片 <input type="file" accept="image/*" style="display:none" onchange="App.presetImageUpload(this,'${_esc(p.key)}')">
                </label>
                <input type="file" id="presetImageInput_${_esc(p.key)}" accept="image/*" style="display:none" onchange="App.presetImageUpload(this,'${_esc(p.key)}')">
            </div>
        </div>`;
    } else if (p.type === 'code') {
        editorHtml = `<div class="preset-editor-header">
            <span class="preset-editor-label">${_esc(p.label)} <code>preset.${_esc(p.key)}</code></span>${draftTag}
        </div>
        <textarea class="input preset-code-area" id="presetCode_${_esc(p.key)}" placeholder="输入 Python 代码（函数体语义，可 return）">${_esc(curValue)}</textarea>
        <div class="preset-code-hint">可引用 report / enterprise / project / carbon_assets / monitoring / formula 对象及公式参数（如 dew_point）；支持 datetime / timedelta / date。</div>`;
    } else {
        // text
        editorHtml = `<div class="preset-editor-header">
            <span class="preset-editor-label">${_esc(p.label)} <code>preset.${_esc(p.key)}</code></span>${draftTag}
        </div>
        <textarea class="input preset-textarea" id="presetText_${_esc(p.key)}" placeholder="输入${_esc(p.label)}...">${_esc(curValue)}</textarea>`;
    }

    editorHtml += `<div class="preset-editor-footer">
        <button class="btn btn-primary btn-sm" onclick="App.savePresetValue('${_esc(p.key)}')">保存</button>
        <button class="btn btn-sm btn-danger-outline" onclick="App.deletePreset('${_esc(p.key)}')">删除</button>
    </div>`;

    container.innerHTML = editorHtml;
}

// 富文本编辑器命令
function richTextCmd(cmd, val) {
    document.execCommand(cmd, false, val || null);
}

// 富文本插入图片
async function richTextInsertImage(input, presetKey) {
    const file = input.files[0];
    if (!file) return;
    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('preset_key', presetKey);
        const resp = await fetch('/api/presets/upload-image', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.status === '成功') {
            document.execCommand('insertImage', false, data.url);
        } else {
            alert('图片上传失败: ' + (data.msg || ''));
        }
    } catch(e) {
        alert('图片上传失败: ' + e.message);
    }
    input.value = '';
}

// 预设图片上传（image 类型）
async function presetImageUpload(input, presetKey) {
    const file = input.files[0];
    if (!file) return;
    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('preset_key', presetKey);
        const resp = await fetch('/api/presets/upload-image', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.status === '成功') {
            // 更新预设 value
            await updatePresetValue(presetKey, data.path);
            // 就地刷新编辑器中的图片预览（不触发开合切换）
            renderPresetEditor(presetKey);
        } else {
            alert('图片上传失败: ' + (data.msg || ''));
        }
    } catch(e) {
        alert('图片上传失败: ' + e.message);
    }
    input.value = '';
}

// 项目预设图片上传（image 类型）
async function projectPresetImageUpload(input, presetKey) {
    const file = input.files[0];
    if (!file) return;
    const pid = projectPresetState.projectId;
    if (!pid) return;
    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('preset_key', presetKey);
        const resp = await fetch('/api/presets/upload-image', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.status === '成功') {
            const result = await API.put('/api/projects/' + pid + '/presets/' + encodeURIComponent(presetKey), { value: data.path });
            if (result.status === '成功') {
                const idx = projectPresetState.presets.findIndex(x => x.key === presetKey);
                if (idx >= 0) projectPresetState.presets[idx].value = data.path;
                renderProjectPresetEditor(presetKey);
                loadVariableReference(pid);
            } else {
                alert('保存失败: ' + (result.msg || ''));
            }
        } else {
            alert('图片上传失败: ' + (data.msg || ''));
        }
    } catch(e) {
        alert('图片上传失败: ' + e.message);
    }
    input.value = '';
}

// 保存预设值
async function savePresetValue(key) {
    const p = presetState.presets.find(x => x.key === key);
    if (!p) return;

    let value;
    if (p.type === 'richtext') {
        const editor = document.getElementById('richtextEditor_' + key);
        value = editor ? editor.innerHTML : '';
    } else if (p.type === 'code') {
        const ta = document.getElementById('presetCode_' + key);
        value = ta ? ta.value : '';
    } else if (p.type === 'text') {
        const ta = document.getElementById('presetText_' + key);
        value = ta ? ta.value : '';
    } else {
        return; // image 通过上传已保存
    }

    await updatePresetValue(key, value);
}

async function updatePresetValue(key, value) {
    const result = await API.put('/api/presets/' + encodeURIComponent(key), { value });
    if (result.status === '成功') {
        // 更新本地缓存
        const idx = presetState.presets.findIndex(x => x.key === key);
        if (idx >= 0) {
            presetState.presets[idx].value = value;
        }
        // 清草稿 + 已保存提示 + 刷新可用字段面板
        afterPresetSaved('enterprise', key);
    } else {
        alert('保存失败: ' + (result.msg || ''));
    }
}

// ── 预设弹窗（新建/编辑元数据）──

function openPresetModal(presetKey) {
    const modal = document.getElementById('presetModal');
    const title = document.getElementById('presetModalTitle');
    const editKey = document.getElementById('presetEditKey');
    const keyInput = document.getElementById('presetKey');
    const labelInput = document.getElementById('presetLabel');
    const typeSel = document.getElementById('presetType');
    const catSel = document.getElementById('presetCategory');
    const error = document.getElementById('presetModalError');

    modal.dataset.mode = ''; // reset to enterprise mode
    const scopeGroup = document.getElementById('presetScopeGroup');
    if (scopeGroup) scopeGroup.classList.add('hidden');
    // 企业预设不支持「自动计算(computed)」「表格(table)」类型，隐藏这些选项
    const computedOpt = document.getElementById('presetTypeComputed');
    if (computedOpt) computedOpt.classList.add('hidden');
    const tableOpt = document.getElementById('presetTypeTable');
    if (tableOpt) tableOpt.classList.add('hidden');

    error.classList.add('hidden');
    error.textContent = '';

    // 填充分类选项
    catSel.innerHTML = '';
    presetState.categories.forEach(c => {
        catSel.innerHTML += `<option value="${_esc(c)}">${_esc(c)}</option>`;
    });
    CustomSelect.refresh(catSel);   // 选项整批换了，同步自定义下拉

    if (presetKey) {
        const p = presetState.presets.find(x => x.key === presetKey);
        if (!p) return;
        title.textContent = '编辑预设';
        editKey.value = p.key;
        keyInput.value = p.key;
        keyInput.disabled = true;
        labelInput.value = p.label || '';
        typeSel.value = p.type || 'text';
        catSel.value = p.category || '自定义';
    } else {
        title.textContent = '新建预设';
        editKey.value = '';
        keyInput.value = '';
        keyInput.disabled = false;
        labelInput.value = '';
        typeSel.value = 'text';
        catSel.value = '自定义';
    }

    modal.classList.remove('hidden');
}

function closePresetModal() {
    const modal = document.getElementById('presetModal');
    modal.classList.add('hidden');
    modal.dataset.mode = '';
}

async function savePreset() {
    const modal = document.getElementById('presetModal');
    const isProject = modal.dataset.mode === 'project';
    const editKey = modal.dataset.presetKey || document.getElementById('presetEditKey').value;
    const key = document.getElementById('presetKey').value.trim();
    const label = document.getElementById('presetLabel').value.trim();
    const type = document.getElementById('presetType').value;
    const category = document.getElementById('presetCategory').value;
    const error = document.getElementById('presetModalError');

    error.classList.add('hidden');

    if (!key || !label) {
        error.textContent = 'Key 和标签不能为空';
        error.classList.remove('hidden');
        return;
    }

    // Route to project / global / enterprise preset
    if (isProject) {
        await saveProjectPreset();
        return;
    }
    if (modal.dataset.mode === 'global') {
        await saveGlobalPreset();
        return;
    }

    let result;
    if (editKey) {
        const body = { label, type, category };
        if (key !== editKey) body.new_key = key;
        result = await API.put('/api/presets/' + encodeURIComponent(editKey), body);
    } else {
        result = await API.post('/api/presets', { key, label, type, value: '', category });
    }

    if (result.status !== '成功') {
        error.textContent = result.msg || '操作失败';
        error.classList.remove('hidden');
        return;
    }

    closePresetModal();
    loadPresets();
}

async function deletePreset(key) {
    if (!confirm('确定删除预设 "' + key + '" 吗？此操作不可恢复。')) return;
    const result = await API.del('/api/presets/' + encodeURIComponent(key));
    if (result.status !== '成功') {
        alert('删除失败: ' + (result.msg || ''));
        return;
    }
    delete presetDrafts.enterprise[key];
    if (presetState.activeKey === key) {
        presetState.activeKey = null;
        hidePresetEditor('enterprise');
    }
    loadPresets();
    loadVariableReference(projectPresetState.projectId || undefined);
}

// ═══════════════════════════════════════════════════════════
// 项目预设（每个项目独立的模板变量）
// ═══════════════════════════════════════════════════════════

async function loadProjectPresets(projectId) {
    const container = document.getElementById('projectPresetList');
    const nameEl = document.getElementById('projectPresetProjectName');
    const addBtn = document.getElementById('btnAddProjectPreset');
    if (!container) return;

    if (!projectId) {
        container.innerHTML = '<p class="muted">请在「生成报告」区域选择一个项目</p>';
        hidePresetEditor('project');
        if (nameEl) nameEl.textContent = '（请先在生成报告区选择项目）';
        if (addBtn) addBtn.disabled = true;
        projectPresetState.projectId = null;
        projectPresetState.presets = [];
        projectPresetState.activeKey = null;
        presetDrafts.project = {};
        return;
    }

    // 切换项目时清空项目预设草稿
    if (projectPresetState.projectId !== projectId) {
        presetDrafts.project = {};
    }

    projectPresetState.projectId = projectId;
    container.innerHTML = '<p class="muted">加载中...</p>';
    if (addBtn) addBtn.disabled = false;

    try {
        const data = await API.get('/api/projects/' + projectId + '/presets');
        if (data.status !== '成功') {
            container.innerHTML = '<p class="muted">加载失败</p>';
            return;
        }
        // 项目预设区域只显示 shared（项目通用字段）+ project（项目独有），
        // global（全项目通用）在独立区域显示
        projectPresetState.presets = (data.presets || []).filter(p => p.scope !== 'global');
        projectPresetState.categories = data.categories || [];
        projectPresetState.activeKey = null;
        renderProjectPresetList();
        hidePresetEditor('project');
        // Update header
        const projSel = document.getElementById('reportProjectSel');
        if (nameEl && projSel) {
            const opt = projSel.selectedOptions[0];
            nameEl.textContent = '（' + (opt ? opt.textContent : '项目#' + projectId) + '）';
        }
    } catch(e) {
        container.innerHTML = '<p class="muted">加载失败: ' + e.message + '</p>';
    }
}


function renderProjectPresetList() {
    const container = document.getElementById('projectPresetList');
    if (!container) return;
    let presets = projectPresetState.presets;
    const grouped = {};
    presets.forEach(p => {
        const cat = p.category || '自定义';
        if (!grouped[cat]) grouped[cat] = [];
        grouped[cat].push(p);
    });
    let catsToRender = getOrderedCategories('project');
    if (!catsToRender.length) {
        container.innerHTML = '<p class="muted">暂无预设，点击「+ 新建」创建</p>';
        return;
    }
    let html = '';
    for (const cat of catsToRender) {
        const items = grouped[cat] || [];
        const isOpen = projectPresetState.expandedCats.has(cat);
        html += '<div class="preset-cat-group">' +
            '<div class="preset-cat-label" draggable="true" onclick="App.togglePresetCat(this)"' +
            ' ondragstart="App.catDragStart(event,\'project\',\'' + _esc(cat) + '\')" ondragend="App.catDragEnd(event)"' +
            ' ondragover="App.presetCatDragOver(event)" ondragleave="App.presetCatDragLeave(event)"' +
            ' ondrop="App.presetCatDrop(event,\'project\',\'' + _esc(cat) + '\')">' +
            '<span class="preset-cat-arrow">' + (isOpen ? '▾' : '▸') + '</span>' + _esc(cat) +
            '<span class="preset-cat-count">(' + items.length + ')</span></div>' +
            '<div class="preset-cat-body' + (isOpen ? '' : ' collapsed') + '"' +
            ' ondragover="App.presetCatDragOver(event)" ondragleave="App.presetCatDragLeave(event)"' +
            ' ondrop="App.presetCatDrop(event,\'project\',\'' + _esc(cat) + '\')">';
        if (!items.length) {
            html += '<div class="preset-cat-empty">暂无字段，可拖拽字段到此分类</div>';
        }
        items.forEach(p => {
            const activeClass = (projectPresetState.activeKey === p.key) ? ' active' : '';
            const typeIcon = {text:'📝',richtext:'📄',image:'🖼',computed:'🔢',code:'🐍',table:'📊'}[p.type] || '📝';
            const hasDraft = presetDrafts.project[p.key] !== undefined;
            const scopeTag = (p.scope === 'shared') ? '<span class="preset-scope-tag" title="所有项目通用字段，各项目分别填值">通用</span>' : '';
            const isRequired = templateRequiredKeys.project.has(p.key);
            const isMissing = isRequired && !((p.value || '').trim());
            const reqClass = (isRequired ? ' template-required' : '') + (isMissing ? ' template-missing' : '');
            html += '<div class="preset-list-item' + activeClass + reqClass + '" draggable="true" data-key="' + _esc(p.key) + '" data-type="' + _esc(p.type || 'text') + '" data-zone="project" ' +
                'onclick="App.selectProjectPreset(\'' + _esc(p.key) + '\')" ' +
                'ondragstart="App.presetDragStart(event,\'project\',\'' + _esc(p.key) + '\')" ' +
                'ondragend="App.presetDragEnd(event)" ' +
                'ondragover="App.presetItemDragOver(event)" ondragleave="App.presetItemDragLeave(event)" ' +
                'ondrop="App.presetItemDrop(event,\'project\',\'' + _esc(p.key) + '\')">' +
                '<span class="preset-type-icon">' + typeIcon + '</span>' +
                '<span class="preset-item-label" title="' + _esc(p.label) + '">' + _esc(p.label) + '</span>' +
                scopeTag +
                (hasDraft ? '<span class="preset-draft-dot" title="有未保存的修改"></span>' : '') +
                '<button class="preset-edit-btn" title="编辑预设参数" onclick="event.stopPropagation();App.openProjectPresetModal(\'' + _esc(p.key) + '\')">✎</button>' +
            '</div>';
        });
        html += '</div></div>';
    }
    container.innerHTML = html;

    if (inlineEditor.zone === 'project' && inlineEditor.key) {
        attachInlineEditor('project', inlineEditor.key, false);
    }
}

function selectProjectPreset(key) {
    selectPresetInline('project', key);
}

// ═══════════════════════════════════════════════════════════
// 全项目通用预设（值对所有项目相同，独立于项目选择）
// ═══════════════════════════════════════════════════════════

async function loadGlobalPresets() {
    const container = document.getElementById('globalPresetList');
    if (!container) return;
    container.innerHTML = '<p class="muted">加载中...</p>';
    try {
        const data = await API.get('/api/project-presets/global');
        if (data.status !== '成功') {
            container.innerHTML = '<p class="muted">加载失败</p>';
            return;
        }
        globalPresetState.presets = data.presets || [];
        globalPresetState.categories = data.categories || [];
        renderGlobalPresetList();
    } catch (e) {
        container.innerHTML = '<p class="muted">加载失败: ' + e.message + '</p>';
    }
}


function renderGlobalPresetList() {
    const container = document.getElementById('globalPresetList');
    if (!container) return;
    let presets = globalPresetState.presets;
    const grouped = {};
    presets.forEach(p => {
        const cat = p.category || '自定义';
        if (!grouped[cat]) grouped[cat] = [];
        grouped[cat].push(p);
    });
    let catsToRender = getOrderedCategories('global');
    if (!catsToRender.length) {
        container.innerHTML = '<p class="muted">暂无预设，点击「+ 新建」创建</p>';
        return;
    }
    let html = '';
    for (const cat of catsToRender) {
        const items = grouped[cat] || [];
        const isOpen = globalPresetState.expandedCats.has(cat);
        html += '<div class="preset-cat-group">' +
            '<div class="preset-cat-label" draggable="true" onclick="App.togglePresetCat(this)"' +
            ' ondragstart="App.catDragStart(event,\'global\',\'' + _esc(cat) + '\')" ondragend="App.catDragEnd(event)"' +
            ' ondragover="App.presetCatDragOver(event)" ondragleave="App.presetCatDragLeave(event)"' +
            ' ondrop="App.presetCatDrop(event,\'global\',\'' + _esc(cat) + '\')">' +
            '<span class="preset-cat-arrow">' + (isOpen ? '▾' : '▸') + '</span>' + _esc(cat) +
            '<span class="preset-cat-count">(' + items.length + ')</span></div>' +
            '<div class="preset-cat-body' + (isOpen ? '' : ' collapsed') + '"' +
            ' ondragover="App.presetCatDragOver(event)" ondragleave="App.presetCatDragLeave(event)"' +
            ' ondrop="App.presetCatDrop(event,\'global\',\'' + _esc(cat) + '\')">';
        if (!items.length) {
            html += '<div class="preset-cat-empty">暂无字段</div>';
        }
        items.forEach(p => {
            const activeClass = (globalPresetState.activeKey === p.key) ? ' active' : '';
            const typeIcon = {text:'📝',richtext:'📄',image:'🖼',computed:'🔢',code:'🐍',table:'📊'}[p.type] || '📝';
            const hasDraft = presetDrafts.global[p.key] !== undefined;
            const isRequired = templateRequiredKeys.project.has(p.key);
            const isMissing = isRequired && !((p.value || '').trim());
            const reqClass = (isRequired ? ' template-required' : '') + (isMissing ? ' template-missing' : '');
            html += '<div class="preset-list-item' + activeClass + reqClass + '" draggable="true" data-key="' + _esc(p.key) + '" data-type="' + _esc(p.type || 'text') + '" data-zone="global" ' +
                'onclick="App.selectGlobalPreset(\'' + _esc(p.key) + '\')" ' +
                'ondragstart="App.presetDragStart(event,\'global\',\'' + _esc(p.key) + '\')" ' +
                'ondragend="App.presetDragEnd(event)" ' +
                'ondragover="App.presetItemDragOver(event)" ondragleave="App.presetItemDragLeave(event)" ' +
                'ondrop="App.presetItemDrop(event,\'global\',\'' + _esc(p.key) + '\')">' +
                '<span class="preset-type-icon">' + typeIcon + '</span>' +
                '<span class="preset-item-label" title="' + _esc(p.label) + '">' + _esc(p.label) + '</span>' +
                (hasDraft ? '<span class="preset-draft-dot" title="有未保存的修改"></span>' : '') +
                '<button class="preset-edit-btn" title="编辑预设参数" onclick="event.stopPropagation();App.openGlobalPresetModal(\'' + _esc(p.key) + '\')">✎</button>' +
                '</div>';
        });
        html += '</div></div>';
    }
    container.innerHTML = html;
    if (inlineEditor.zone === 'global' && inlineEditor.key) {
        attachInlineEditor('global', inlineEditor.key, false);
    }
}

function selectGlobalPreset(key) {
    selectPresetInline('global', key);
}

// ═══════════════════════════════════════════════════════════
// 预设批量编辑（Excel 导入导出）
// ═══════════════════════════════════════════════════════════

function openPresetBatchModal() {
    const modal = document.getElementById('presetBatchModal');
    if (!modal) return;
    // 填充项目列表
    const container = document.getElementById('batchPresetProjectList');
    if (container && state.projects.length) {
        let html = '';
        state.projects.forEach(p => {
            html += `<label class="batch-proj-item">
                <input type="checkbox" class="preset-batch-cb" value="${p.id}">
                <span>${_esc(p.project_name)}</span>
            </label>`;
        });
        container.innerHTML = html;
    }
    document.getElementById('presetBatchFileName').textContent = '未选择文件';
    document.getElementById('presetBatchStatus').classList.add('hidden');
    document.getElementById('presetBatchDetail').innerHTML = '';
    modal.classList.remove('hidden');
}

function closePresetBatchModal() {
    const modal = document.getElementById('presetBatchModal');
    if (modal) modal.classList.add('hidden');
}

function presetBatchToggleAll(checked) {
    document.querySelectorAll('.preset-batch-cb').forEach(cb => { cb.checked = checked; });
}

function getSelectedPresetBatchIds() {
    const ids = [];
    document.querySelectorAll('.preset-batch-cb:checked').forEach(cb => ids.push(parseInt(cb.value)));
    return ids;
}

async function exportPresetBatch() {
    const ids = getSelectedPresetBatchIds();
    const statusEl = document.getElementById('presetBatchStatus');
    if (!ids.length) { alert('请先勾选要编辑的项目'); return; }
    statusEl.className = 'status-msg';
    statusEl.textContent = '⏳ 正在生成 Excel...';
    statusEl.classList.remove('hidden');
    try {
        const resp = await fetch('/api/presets/batch-export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_ids: ids }),
        });
        if (!resp.ok) {
            let msg = '导出失败';
            try { msg = (await resp.json()).msg || msg; } catch(_) {}
            statusEl.className = 'status-msg error';
            statusEl.textContent = '❌ ' + msg;
            return;
        }
        const blob = await resp.blob();
        const disp = resp.headers.get('Content-Disposition') || '';
        let filename = '预设批量编辑.xlsx';
        const m = disp.match(/filename\*=UTF-8''([^;]+)/);
        if (m) { try { filename = decodeURIComponent(m[1]); } catch(_) {} }
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
        statusEl.className = 'status-msg success';
        statusEl.textContent = '✅ 已导出，修改后上传导入即可批量更新';
    } catch (e) {
        statusEl.className = 'status-msg error';
        statusEl.textContent = '❌ 网络错误: ' + e.message;
    }
}

async function importPresetBatch() {
    const ids = getSelectedPresetBatchIds();
    const fileInput = document.getElementById('presetBatchFile');
    const statusEl = document.getElementById('presetBatchStatus');
    const detailEl = document.getElementById('presetBatchDetail');
    if (!ids.length) { alert('请先勾选要导入的项目'); return; }
    if (!fileInput.files.length) { alert('请先选择 .xlsx 文件'); return; }

    statusEl.className = 'status-msg';
    statusEl.textContent = '⏳ 正在导入...';
    statusEl.classList.remove('hidden');
    detailEl.innerHTML = '';

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    fd.append('project_ids', ids.join(','));
    try {
        const resp = await fetch('/api/presets/batch-import', { method: 'POST', body: fd });
        const data = await resp.json();
        if (data.status === '成功') {
            statusEl.className = 'status-msg success';
            statusEl.textContent = `✅ 导入完成：更新 ${data.imported} 个值`;
            if (data.errors && data.errors.length) {
                detailEl.innerHTML = '<div style="color:var(--warning);font-weight:600">部分跳过：</div>' +
                    data.errors.map(e => '<div style="color:var(--text-gray)">· ' + _esc(e) + '</div>').join('');
            }
            loadPresets();
            loadGlobalPresets();
            if (projectPresetState.projectId) loadProjectPresets(projectPresetState.projectId);
        } else {
            statusEl.className = 'status-msg error';
            statusEl.textContent = '❌ 导入失败: ' + (data.msg || '未知错误');
        }
    } catch (e) {
        statusEl.className = 'status-msg error';
        statusEl.textContent = '❌ 网络错误: ' + e.message;
    }
}

/* ═══════════════════════════════════════════════════════════
   表格(table)预设编辑器
   ═══════════════════════════════════════════════════════════ */

function parseTableValue(v) {
    let data = {};
    try { data = (typeof v === 'string' && v.trim()) ? JSON.parse(v) : (v || {}); } catch (e) { data = {}; }
    if (!data || typeof data !== 'object' || Array.isArray(data)) data = {};
    return {
        generator: data.generator || '',
        headers: Array.isArray(data.headers) ? data.headers : [],
        rows: Array.isArray(data.rows) ? data.rows : [],
    };
}

// 表格预设的自动生成器。value 与后端 core/project_presets.py::_PROJECTION_TABLES 对应。
// 顺序即下拉框顺序：manual 之外的三项都由项目数据实时算出，不用手填。
const TABLE_GENERATORS = [
    { id: 'projection_10yr', label: '自动（十年预估）', hint: '生成报告时按当前项目自动生成十年逐年预估表（年份 / 基准线排放 / 项目排放 / 泄漏 / 减排量，含合计与计入期年数）。' },
    { id: 'projection_10yr_baseline', label: '自动（十年预估）（基准线）', hint: '自动生成表 B.2：只列基准线排放（年份 / 基准线排放，含合计）。' },
    { id: 'projection_10yr_project', label: '自动（十年预估）（项目排放）', hint: '自动生成表 B.3：只列项目排放（年份 / 项目排放，含合计）。' },
];
const TABLE_GENERATOR_IDS = TABLE_GENERATORS.map(g => g.id);
const TABLE_GENERATOR_HINTS = TABLE_GENERATORS.reduce((m, g) => (m[g.id] = g.hint, m), {});

function tableEditorHtml(prefix, key, data) {
    const id = prefix + 'Table_' + key;
    // 之前只认 projection_10yr，另两个 generator 会掉进「手动编辑」分支显示空白表格，
    // 而且一保存就被 collectTableValue 用空表覆盖掉 generator。
    const gen = TABLE_GENERATOR_IDS.includes(data.generator) ? data.generator : '';
    const isAuto = !!gen;
    const headers = data.headers.length ? data.headers : ['列1', '列2'];
    let html = '<div class="preset-editor-header">表格预设 <code>project_preset.' + _esc(key) + '</code></div>';
    html += '<div class="form-group"><label>数据来源</label>' +
        '<select id="' + id + '_src" class="input custom-select" data-placeholder="选择数据来源" ' +
        'onchange="App.tableToggleSrc(\'' + prefix + '\',\'' + _esc(key) + '\')">' +
        '<option value="manual"' + (isAuto ? '' : ' selected') + '>手动编辑</option>' +
        TABLE_GENERATORS.map(g =>
            '<option value="' + g.id + '"' + (gen === g.id ? ' selected' : '') + '>' + _esc(g.label) + '</option>'
        ).join('') +
        '</select></div>';
    html += '<div id="' + id + '_manual"' + (isAuto ? ' style="display:none"' : '') + '>';
    html += '<table class="preset-table-grid" id="' + id + '_grid"><thead><tr id="' + id + '_headrow">';
    headers.forEach(h => {
        html += '<th><a class="pt-del-col" onclick="App.tableDelCol(this)">✕</a><input class="input pt-head" value="' + _esc(h) + '"></th>';
    });
    html += '<th class="pt-add-col"><a onclick="App.tableAddCol(\'' + prefix + '\',\'' + _esc(key) + '\')">+列</a></th></tr></thead><tbody id="' + id + '_body">';
    data.rows.forEach(r => {
        html += '<tr>';
        headers.forEach((h, ci) => {
            html += '<td><input class="input pt-cell" value="' + _esc(r[ci] != null ? r[ci] : '') + '"></td>';
        });
        html += '<td class="pt-del-row"><a onclick="App.tableDelRow(this)">✕</a></td></tr>';
    });
    html += '</tbody></table>';
    html += '<button class="btn btn-sm btn-outline" onclick="App.tableAddRow(\'' + prefix + '\',\'' + _esc(key) + '\')">+行</button>';
    html += '</div>';
    html += '<div id="' + id + '_auto"' + (isAuto ? '' : ' style="display:none"') +
        '><p class="preset-editor-auto-hint" id="' + id + '_autohint">' +
        _esc(TABLE_GENERATOR_HINTS[gen] || '') + '</p></div>';
    return html;
}

function tableToggleSrc(prefix, key) {
    const id = prefix + 'Table_' + key;
    const sel = document.getElementById(id + '_src');
    const src = sel ? sel.value : 'manual';
    const isAuto = TABLE_GENERATOR_IDS.includes(src);
    document.getElementById(id + '_manual').style.display = isAuto ? 'none' : '';
    document.getElementById(id + '_auto').style.display = isAuto ? '' : 'none';
    // 说明文案随选中的生成器变（B.4 / B.2 / B.3 列不一样）
    const hint = document.getElementById(id + '_autohint');
    if (hint) hint.textContent = TABLE_GENERATOR_HINTS[src] || '';
}

function tableAddRow(prefix, key) {
    const id = prefix + 'Table_' + key;
    const body = document.getElementById(id + '_body');
    const colCount = document.querySelectorAll('#' + id + '_headrow th input.pt-head').length;
    const tr = document.createElement('tr');
    for (let i = 0; i < colCount; i++) {
        const td = document.createElement('td');
        td.innerHTML = '<input class="input pt-cell">';
        tr.appendChild(td);
    }
    const del = document.createElement('td');
    del.className = 'pt-del-row';
    del.innerHTML = '<a onclick="App.tableDelRow(this)">✕</a>';
    tr.appendChild(del);
    body.appendChild(tr);
}

function tableDelRow(a) { a.closest('tr').remove(); }

function tableAddCol(prefix, key) {
    const id = prefix + 'Table_' + key;
    const headRow = document.getElementById(id + '_headrow');
    const n = headRow.querySelectorAll('th input.pt-head').length;
    const th = document.createElement('th');
    th.innerHTML = '<a class="pt-del-col" onclick="App.tableDelCol(this)">✕</a><input class="input pt-head" value="列' + (n + 1) + '">';
    headRow.insertBefore(th, headRow.lastElementChild);
    const body = document.getElementById(id + '_body');
    body.querySelectorAll('tr').forEach(tr => {
        const td = document.createElement('td');
        td.innerHTML = '<input class="input pt-cell">';
        tr.insertBefore(td, tr.lastElementChild);
    });
}

function tableDelCol(a) {
    const th = a.closest('th');
    const headRow = th.parentNode;
    const ci = Array.prototype.indexOf.call(headRow.children, th);
    th.remove();
    headRow.closest('table').querySelectorAll('tbody tr').forEach(tr => {
        if (tr.children[ci]) tr.children[ci].remove();
    });
}

function collectTableValue(prefix, key) {
    const id = prefix + 'Table_' + key;
    const srcEl = document.getElementById(id + '_src');
    // 回写用户实际选中的那个生成器（原来写死 projection_10yr，
    // 会把 B.2/B.3 的 generator 覆盖成 B.4 的）
    if (srcEl && TABLE_GENERATOR_IDS.includes(srcEl.value)) {
        return JSON.stringify({ generator: srcEl.value });
    }
    const headers = [];
    document.querySelectorAll('#' + id + '_headrow th input.pt-head').forEach(inp => headers.push(inp.value));
    const rows = [];
    document.querySelectorAll('#' + id + '_body tr').forEach(tr => {
        const row = [];
        tr.querySelectorAll('input.pt-cell').forEach(inp => row.push(inp.value));
        rows.push(row);
    });
    return JSON.stringify({ headers: headers, rows: rows });
}

function renderGlobalPresetEditor(key) {
    const container = document.getElementById('globalPresetEditorInline');
    if (!container) return;
    const p = globalPresetState.presets.find(x => x.key === key);
    if (!p) { closeInlineEditor(false); return; }
    const draft = presetDrafts.global[key];
    const curValue = draft !== undefined ? draft : (p.value || '');
    const draftTag = draft !== undefined ? '<span class="tag tag-warn">未保存</span>' : '';
    const footer = '<div class="preset-editor-footer">' +
        '<button class="btn btn-primary btn-sm" onclick="App.saveGlobalPresetValue(\'' + _esc(p.key) + '\')">保存</button>' +
        '<button class="btn btn-sm btn-danger-outline" onclick="App.deleteGlobalPreset(\'' + _esc(p.key) + '\')">删除</button>' +
        '</div>';
    let html = '';
    if (p.type === 'computed') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' + draftTag + '</div>' +
            '<div class="form-group"><label>计算表达式</label>' +
            '<input type="text" id="gpVal_' + _esc(p.key) + '" class="input" value="' + _esc(curValue) + '"></div>' + footer;
    } else if (p.type === 'richtext') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' + draftTag + '</div>' +
            '<div class="richtext-editor" id="gpRichtext_' + _esc(p.key) + '" contenteditable="true">' + curValue + '</div>' + footer;
    } else if (p.type === 'code') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' + draftTag + '</div>' +
            '<textarea class="input preset-code-area" id="gpCode_' + _esc(p.key) + '" placeholder="输入 Python 代码（函数体语义，可 return）">' + _esc(curValue) + '</textarea>' +
            '<div class="preset-code-hint">代码对所有项目共享，可引用 report / enterprise / project / carbon_assets 等对象，也可引用其它预设 project_preset.xxx（值会随各项目自动变化）。</div>' + footer;
    } else if (p.type === 'image') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span></div>' +
            '<div class="preset-image-area">' +
            (p.value ? '<img src="/api/presets/images/' + _esc(p.value.split(/[\\/]/).pop()) + '" class="preset-preview-img">' : '<div class="preset-image-placeholder">暂无图片</div>') +
            '<label class="btn btn-sm btn-outline" style="cursor:pointer;margin-top:8px">上传图片 ' +
            '<input type="file" accept="image/*" style="display:none" onchange="App.globalPresetImageUpload(this,\'' + _esc(p.key) + '\')"></label></div>' +
            '<div class="preset-editor-footer"><button class="btn btn-sm btn-danger-outline" onclick="App.deleteGlobalPreset(\'' + _esc(p.key) + '\')">删除</button></div>';
    } else if (p.type === 'table') {
        html = tableEditorHtml('gp', p.key, parseTableValue(curValue)) + footer;
    } else {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' + draftTag + '</div>' +
            '<textarea class="input preset-textarea" id="gpText_' + _esc(p.key) + '" placeholder="输入' + _esc(p.label) + '...">' + _esc(curValue) + '</textarea>' + footer;
    }
    container.innerHTML = html;
    // 编辑器是动态注入的，CustomSelect 的启动扫描扫不到，得就地初始化
    CustomSelect.initAll(container);
}

async function saveGlobalPresetValue(key) {
    let value;
    const p = globalPresetState.presets.find(x => x.key === key);
    if (!p) return;
    if (p.type === 'richtext') {
        const el = document.getElementById('gpRichtext_' + key);
        value = el ? el.innerHTML : '';
    } else if (p.type === 'code') {
        const el = document.getElementById('gpCode_' + key);
        value = el ? el.value : '';
    } else if (p.type === 'computed') {
        const el = document.getElementById('gpVal_' + key);
        value = el ? el.value : '';
    } else if (p.type === 'text') {
        const el = document.getElementById('gpText_' + key);
        value = el ? el.value : '';
    } else if (p.type === 'table') {
        value = collectTableValue('gp', key);
    } else {
        return;
    }
    const data = await API.put('/api/project-presets/global/' + encodeURIComponent(key), { value: value });
    if (data.status === '成功') {
        const idx = globalPresetState.presets.findIndex(x => x.key === key);
        if (idx >= 0) globalPresetState.presets[idx].value = value;
        afterPresetSaved('global', key);
    } else {
        alert('保存失败: ' + (data.msg || '未知错误'));
    }
}

async function deleteGlobalPreset(key) {
    if (!confirm('确定删除全项目通用预设 "' + key + '" 吗？删除后所有项目都不再使用该字段。')) return;
    const result = await API.del('/api/project-presets/global/' + encodeURIComponent(key));
    if (result.status !== '成功') {
        alert('删除失败: ' + (result.msg || ''));
        return;
    }
    delete presetDrafts.global[key];
    if (globalPresetState.activeKey === key) {
        globalPresetState.activeKey = null;
        hidePresetEditor('global');
    }
    loadGlobalPresets();
    loadVariableReference(projectPresetState.projectId || undefined);
}

async function globalPresetImageUpload(input, key) {
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    fd.append('preset_key', key);
    try {
        const resp = await fetch('/api/presets/upload-image', { method: 'POST', body: fd });
        const data = await resp.json();
        if (data.status === '成功') {
            const result = await API.put('/api/project-presets/global/' + encodeURIComponent(key), { value: data.path });
            if (result.status === '成功') {
                const idx = globalPresetState.presets.findIndex(x => x.key === key);
                if (idx >= 0) globalPresetState.presets[idx].value = data.path;
                renderGlobalPresetEditor(key);
            }
        } else {
            alert('图片上传失败: ' + (data.msg || ''));
        }
    } catch (e) {
        alert('图片上传失败: ' + e.message);
    }
    input.value = '';
}

function openGlobalPresetModal(presetKey) {
    const item = presetKey ? (globalPresetState.presets.find(p => p.key === presetKey) || null) : null;
    document.getElementById('presetModalTitle').textContent = presetKey ? '编辑预设属性' : '新建全项目通用预设';
    const computedOpt = document.getElementById('presetTypeComputed');
    if (computedOpt) computedOpt.classList.remove('hidden');
    const tableOpt = document.getElementById('presetTypeTable');
    if (tableOpt) tableOpt.classList.remove('hidden');
    document.getElementById('presetKey').value = presetKey || '';
    document.getElementById('presetKey').disabled = !!presetKey;
    document.getElementById('presetLabel').value = item ? item.label || '' : '';
    document.getElementById('presetType').value = item ? item.type || 'text' : 'text';
    const catSel = document.getElementById('presetCategory');
    catSel.innerHTML = '';
    (globalPresetState.categories || []).forEach(c => {
        catSel.innerHTML += '<option value="' + _esc(c) + '">' + _esc(c) + '</option>';
    });
    CustomSelect.refresh(catSel);   // 选项整批换了，同步自定义下拉
    catSel.value = item ? item.category || '自定义' : '自定义';
    CustomSelect.refresh(catSel);
    document.getElementById('presetSortOrder').value = item ? item.sort_order || '' : '';
    const scopeGroup = document.getElementById('presetScopeGroup');
    if (scopeGroup) scopeGroup.classList.add('hidden'); // 全项目通用固定，不显示应用范围
    document.getElementById('presetEditKey').value = '';
    document.getElementById('presetModal').classList.remove('hidden');
    document.getElementById('presetModal').dataset.presetKey = presetKey || '';
    document.getElementById('presetModal').dataset.mode = 'global';
    document.getElementById('presetModalError').classList.add('hidden');
    document.getElementById('presetModalError').textContent = '';
}

async function saveGlobalPreset() {
    const modal = document.getElementById('presetModal');
    const existingKey = modal.dataset.presetKey;
    const key = document.getElementById('presetKey').value.trim();
    const label = document.getElementById('presetLabel').value.trim();
    const type = document.getElementById('presetType').value;
    const category = document.getElementById('presetCategory').value;
    const sortOrder = document.getElementById('presetSortOrder').value;
    const errorEl = document.getElementById('presetModalError');

    if (!key || !label) {
        errorEl.textContent = 'Key 和标签不能为空';
        errorEl.classList.remove('hidden');
        return;
    }
    try {
        let result;
        if (existingKey) {
            result = await API.put('/api/project-presets/global/' + encodeURIComponent(existingKey),
                { new_key: key, label, type, category, sort_order: sortOrder ? parseInt(sortOrder) : undefined });
        } else {
            result = await API.post('/api/project-presets/global',
                { key, label, type, category, sort_order: sortOrder ? parseInt(sortOrder) : undefined });
        }
        if (result.status !== '成功') {
            errorEl.textContent = result.msg || '操作失败';
            errorEl.classList.remove('hidden');
            return;
        }
        modal.classList.add('hidden');
        loadGlobalPresets();
    } catch (e) {
        errorEl.textContent = '网络错误: ' + e.message;
        errorEl.classList.remove('hidden');
    }
}

function renderProjectPresetEditor(key) {
    const container = document.getElementById('projectPresetEditorInline');
    if (!container) return;
    const p = projectPresetState.presets.find(x => x.key === key);
    if (!p) {
        closeInlineEditor(false);
        return;
    }
    const draft = presetDrafts.project[key];
    const curValue = draft !== undefined ? draft : (p.value || '');
    const draftTag = draft !== undefined ? '<span class="tag tag-warn">未保存</span>' : '';
    const footer = '<div class="preset-editor-footer">' +
        '<button class="btn btn-primary btn-sm" onclick="App.saveProjectPresetValue(\'' + _esc(p.key) + '\')">保存</button>' +
        '<button class="btn btn-sm btn-danger-outline" onclick="App.deleteProjectPreset(\'' + _esc(p.key) + '\')">删除</button>' +
        '</div>';
    let html = '';
    if (p.type === 'computed') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' +
            '<span class="tag tag-blue">自动计算</span>' + draftTag + '</div>' +
            '<div class="form-group"><label>计算表达式</label>' +
            '<input type="text" id="ppVal_' + _esc(p.key) + '" class="input" value="' + _esc(curValue) + '">' +
            '</div>' +
            '<div class="form-group"><label>引用字段：<code>estimated_annual_reduction</code> <code>pending_validation_10yr</code> 等</label></div>' +
            footer;
    } else if (p.type === 'richtext') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' + draftTag + '</div>' +
            '<div class="richtext-toolbar">' +
            '<button onclick="App.richTextCmd(\'bold\')" title="加粗"><b>B</b></button>' +
            '<button onclick="App.richTextCmd(\'italic\')" title="斜体"><i>I</i></button>' +
            '<button onclick="App.richTextCmd(\'underline\')" title="下划线"><u>U</u></button>' +
            '</div>' +
            '<div class="richtext-editor" id="ppRichtext_' + _esc(p.key) + '" contenteditable="true">' + curValue + '</div>' +
            footer;
    } else if (p.type === 'image') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span></div>' +
            '<div class="preset-image-area">' +
            (p.value ? '<img src="/api/presets/images/' + _esc(p.value.split(/[\\/]/).pop()) + '" class="preset-preview-img">' : '<div class="preset-image-placeholder">暂无图片</div>') +
            '<label class="btn btn-sm btn-outline" style="cursor:pointer;margin-top:8px">上传图片 ' +
            '<input type="file" accept="image/*" style="display:none" onchange="App.projectPresetImageUpload(this,\'' + _esc(p.key) + '\')"></label></div>' +
            '<div class="preset-editor-footer">' +
            '<button class="btn btn-sm btn-danger-outline" onclick="App.deleteProjectPreset(\'' + _esc(p.key) + '\')">删除</button>' +
            '</div>';
    } else if (p.type === 'code') {
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' + draftTag + '</div>' +
            '<textarea class="input preset-code-area" id="ppCode_' + _esc(p.key) + '" placeholder="输入 Python 代码（函数体语义，可 return）">' + _esc(curValue) + '</textarea>' +
            '<div class="preset-code-hint">可引用 report / enterprise / project / carbon_assets / monitoring / formula 对象及公式参数（如 dew_point）。</div>' +
            footer;
    } else if (p.type === 'table') {
        html = tableEditorHtml('pp', p.key, parseTableValue(curValue)) + footer;
    } else {
        // text
        html = '<div class="preset-editor-header"><span class="preset-editor-label">' + _esc(p.label) +
            ' <code>project_preset.' + _esc(p.key) + '</code></span>' + draftTag + '</div>' +
            '<textarea id="ppVal_' + _esc(p.key) + '" class="input preset-textarea">' + _esc(curValue) + '</textarea>' +
            footer;
    }
    container.innerHTML = html;
    // 编辑器是动态注入的，CustomSelect 的启动扫描扫不到，得就地初始化
    CustomSelect.initAll(container);
}

async function saveProjectPresetValue(key) {
    let value;
    const p = projectPresetState.presets.find(x => x.key === key);
    if (!p) return;
    if (p.type === 'richtext') {
        const editor = document.getElementById('ppRichtext_' + key);
        value = editor ? editor.innerHTML : '';
    } else if (p.type === 'code') {
        const ta = document.getElementById('ppCode_' + key);
        value = ta ? ta.value : '';
    } else if (p.type === 'table') {
        value = collectTableValue('pp', key);
    } else {
        const input = document.getElementById('ppVal_' + key);
        value = input ? input.value : '';
    }
    const pid = projectPresetState.projectId;
    if (!pid) return;
    const data = await API.put('/api/projects/' + pid + '/presets/' + encodeURIComponent(key), { value: value });
    if (data.status === '成功') {
        const idx = projectPresetState.presets.findIndex(x => x.key === key);
        if (idx >= 0) projectPresetState.presets[idx].value = value;
        // 清草稿 + 已保存提示 + 刷新可用字段面板
        afterPresetSaved('project', key);
    } else {
        alert('保存失败: ' + (data.msg || '未知错误'));
    }
}

function openProjectPresetModal(presetKey) {
    const item = presetKey ? (projectPresetState.presets.find(p => p.key === presetKey) || null) : null;
    document.getElementById('presetModalTitle').textContent = presetKey ? '编辑预设属性' : '新建项目预设';
    // 项目预设支持「自动计算(computed)」「表格(table)」类型，显示这些选项
    const computedOpt = document.getElementById('presetTypeComputed');
    if (computedOpt) computedOpt.classList.remove('hidden');
    const tableOpt = document.getElementById('presetTypeTable');
    if (tableOpt) tableOpt.classList.remove('hidden');
    document.getElementById('presetKey').value = presetKey || '';
    document.getElementById('presetKey').disabled = !!presetKey;
    document.getElementById('presetLabel').value = item ? item.label || '' : '';
    document.getElementById('presetType').value = item ? item.type || 'text' : 'text';
    // 分类选项使用项目预设的分类集合
    const catSel = document.getElementById('presetCategory');
    catSel.innerHTML = '';
    (projectPresetState.categories || []).forEach(c => {
        catSel.innerHTML += '<option value="' + _esc(c) + '">' + _esc(c) + '</option>';
    });
    CustomSelect.refresh(catSel);   // 选项整批换了，同步自定义下拉
    catSel.value = item ? item.category || '自定义' : '自定义';
    CustomSelect.refresh(catSel);
    document.getElementById('presetSortOrder').value = item ? item.sort_order || '' : '';
    // 应用范围（仅项目预设显示）
    const scopeGroup = document.getElementById('presetScopeGroup');
    if (scopeGroup) scopeGroup.classList.remove('hidden');
    const scopeSel = document.getElementById('presetScope');
    if (scopeSel) scopeSel.value = (item && item.scope === 'shared') ? 'shared' : 'project';
    document.getElementById('presetEditKey').value = ''; // clear enterprise state
    document.getElementById('presetModal').classList.remove('hidden');
    document.getElementById('presetModal').dataset.presetKey = presetKey || '';
    document.getElementById('presetModal').dataset.mode = 'project'; // mark as project preset
    document.getElementById('presetModalError').classList.add('hidden');
    document.getElementById('presetModalError').textContent = '';
}

async function saveProjectPreset() {
    const modal = document.getElementById('presetModal');
    const existingKey = modal.dataset.presetKey;
    const key = document.getElementById('presetKey').value.trim();
    const label = document.getElementById('presetLabel').value.trim();
    const type = document.getElementById('presetType').value;
    const category = document.getElementById('presetCategory').value;
    const sortOrder = document.getElementById('presetSortOrder').value;
    const scopeEl = document.getElementById('presetScope');
    const scope = scopeEl ? scopeEl.value : 'project';
    const errorEl = document.getElementById('presetModalError');
    const pid = projectPresetState.projectId;

    if (!key || !label) {
        errorEl.textContent = 'Key 和标签不能为空';
        errorEl.classList.remove('hidden');
        return;
    }
    if (!pid) return;

    try {
        let result;
        if (existingKey) {
            result = await API.put('/api/projects/' + pid + '/presets/' + encodeURIComponent(existingKey),
                { new_key: key, label, type, category, scope, sort_order: sortOrder ? parseInt(sortOrder) : undefined });
        } else {
            result = await API.post('/api/projects/' + pid + '/presets',
                { key, label, type, category, scope, sort_order: sortOrder ? parseInt(sortOrder) : undefined });
        }
        if (result.status !== '成功') {
            errorEl.textContent = result.msg || '操作失败';
            errorEl.classList.remove('hidden');
            return;
        }
        modal.classList.add('hidden');
        loadProjectPresets(pid);
        // scope 可能在 project/shared/global 之间变化，三区都刷新
        loadGlobalPresets();
    } catch(e) {
        errorEl.textContent = '网络错误: ' + e.message;
        errorEl.classList.remove('hidden');
    }
}

async function deleteProjectPreset(key) {
    const pid = projectPresetState.projectId;
    if (!pid) return;
    const p = projectPresetState.presets.find(x => x.key === key);
    const isShared = p && p.scope === 'shared';
    const msg = isShared
        ? '「' + (p.label || key) + '」是所有项目通用字段。\n删除后所有项目都不再显示该字段，各项目已填写的值也会一并清除。\n确定删除吗？'
        : '确定删除预设 "' + key + '" 吗？';
    if (!confirm(msg)) return;
    const result = await API.del('/api/projects/' + pid + '/presets/' + encodeURIComponent(key));
    if (result.status !== '成功') {
        alert('删除失败: ' + (result.msg || ''));
        return;
    }
    delete presetDrafts.project[key];
    if (projectPresetState.activeKey === key) {
        projectPresetState.activeKey = null;
        hidePresetEditor('project');
    }
    loadProjectPresets(pid);
    loadVariableReference(pid);
}

// ── 报告历史 ──
async function loadReportHistory() {
    const container = document.getElementById('reportHistory');
    if (!container) return;

    let url = '/api/reports';
    if (state.reportHistoryMode === 'project') {
        const pid = document.getElementById('reportProjectSel').value;
        if (!pid) {
            container.innerHTML = '<p class="muted" style="text-align:center;padding:20px">请先在生成报告中选择项目</p>';
            syncHistoryModeButtons();
            return;
        }
        url += '?project_id=' + encodeURIComponent(pid);
    }

    const data = await API.get(url);
    syncHistoryModeButtons();

    if (!data.reports || !data.reports.length) {
        container.innerHTML = '<p class="muted" style="text-align:center;padding:20px">暂无报告</p>';
        return;
    }
    let html = '<div class="report-history-list">';
    data.reports.forEach(r => {
        const size = r.file_size_bytes ? (r.file_size_bytes / 1024).toFixed(1) + ' KB' : '—';
        const typeLabels = {verification:'核查报告', pdd:'项目设计文档（PDD）'};
        const typeLabel = typeLabels[r.report_type] || r.report_type;
        const icon = {verification:'📊', pdd:'📘'}[r.report_type] || '📄';
        html += `<div class="report-history-item">
            <div class="rpt-icon">${icon}</div>
            <div class="rpt-info">
                <div class="rpt-title">${_esc(r.report_title)}</div>
                <div class="rpt-meta">${_esc(typeLabel)} · ${size} · ${_esc(r.generated_at)}${r.file_available === false ? ' · <span class="muted">原文件缺失，请从备份恢复</span>' : ''}</div>
            </div>
            ${r.file_available === false
                ? '<button type="button" class="btn btn-sm btn-outline rpt-dl-btn" disabled title="历史登记仍保留，请从原部署或备份恢复原件">原文件缺失</button>'
                : `<button type="button" class="btn btn-sm btn-primary rpt-dl-btn" onclick="App.downloadReport('/api/reports/${Number(r.id)}/download')">📥 下载</button>`}
        </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
}

// 切换报告历史显示模式：全部 / 选中项目
function setReportHistoryMode(mode) {
    state.reportHistoryMode = (mode === 'project') ? 'project' : 'all';
    loadReportHistory();
}

// 同步「全部 / 选中项目」按钮的激活态
function syncHistoryModeButtons() {
    const btnAll = document.getElementById('historyModeAll');
    const btnProject = document.getElementById('historyModeProject');
    if (!btnAll || !btnProject) return;
    const isAll = state.reportHistoryMode !== 'project';
    btnAll.className = 'btn btn-sm ' + (isAll ? 'btn-primary' : 'btn-outline');
    btnProject.className = 'btn btn-sm ' + (isAll ? 'btn-outline' : 'btn-primary');
}

/* ── 预设分类折叠（带高度动画）── */
function togglePresetCat(labelEl) {
    const body = labelEl.nextElementSibling;
    if (!body) return;
    const arrow = labelEl.querySelector('.preset-cat-arrow');
    // 记录展开状态，列表重渲染后恢复
    const zoneEl = labelEl.closest('.preset-zone');
    const zone = zoneEl ? zoneEl.getAttribute('data-zone') : 'enterprise';
    const st = getPresetStore(zone);
    const catName = labelEl.textContent.replace(/[▸▾]/, '').replace(/\(\d+\)/, '').trim();

    if (body.classList.contains('collapsed')) {
        body.classList.remove('collapsed');
        animateMaxHeight(body, true);
        if (arrow) arrow.textContent = '▾';
        st.expandedCats.add(catName);
    } else {
        animateMaxHeight(body, false, () => body.classList.add('collapsed'));
        if (arrow) arrow.textContent = '▸';
        st.expandedCats.delete(catName);
    }
}

/* ── 预设编辑器显隐 ── */
function hidePresetEditor(zone) {
    if (inlineEditor.zone === zone) {
        if (inlineEditor.key) delete presetDrafts[zone][inlineEditor.key];
        closeInlineEditor(false);
    } else {
        removeInlineEditorDom();
    }
}

/* ── 预设字段拖拽（企业预设 ↔ 项目预设） ── */
let dragPreset = null; // { zone, key, type }

function presetDragStart(e, zone, key) {
    const item = e.target && e.target.closest ? e.target.closest('.preset-list-item') : null;
    const type = item ? item.getAttribute('data-type') : 'text';
    dragPreset = { zone, key, type };
    if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', key); } catch(_) {}
    }
    if (item) item.classList.add('dragging');
}

function presetDragEnd(e) {
    const item = e.target && e.target.closest ? e.target.closest('.preset-list-item') : null;
    if (item) item.classList.remove('dragging');
    clearPresetZoneHighlight();
    document.querySelectorAll('.preset-list-item.drop-target').forEach(el => el.classList.remove('drop-target'));
    document.querySelectorAll('.preset-cat-label.drag-over, .preset-cat-body.drag-over').forEach(el => el.classList.remove('drag-over'));
    dragPreset = null;
}

/* ── 区域内拖拽：预设块 ↔ 预设块（换分类/排序），预设块 → 分类 ── */

function presetItemDragOver(e) {
    if (!dragPreset) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    const item = e.target && e.target.closest ? e.target.closest('.preset-list-item') : null;
    if (item && item.getAttribute('data-key') !== dragPreset.key) item.classList.add('drop-target');
}

function presetItemDragLeave(e) {
    const item = e.target && e.target.closest ? e.target.closest('.preset-list-item') : null;
    if (item) item.classList.remove('drop-target');
}

// 拖到某个预设块上：同区域 → 移到该块所在分类并排到它后面；跨区域 → 原有移动逻辑
async function presetItemDrop(e, zone, targetKey) {
    e.preventDefault();
    e.stopPropagation();
    const src = dragPreset;
    dragPreset = null;
    clearPresetZoneHighlight();
    document.querySelectorAll('.preset-list-item.drop-target').forEach(el => el.classList.remove('drop-target'));
    if (!src || src.key === targetKey) return;
    if (src.zone !== zone) {
        // 跨区域：移到目标块所在分类
        const st = getPresetStore(zone);
        const target = st.presets.find(x => x.key === targetKey);
        movePresetField(src, zone, target ? (target.category || '自定义') : undefined);
        return;
    }
    await reorderPresetWithinZone(zone, src.key, targetKey);
}

function presetCatDragOver(e) {
    if (!dragPreset && !dragCat) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    const el = e.target && e.target.closest ? e.target.closest('.preset-cat-label, .preset-cat-body') : null;
    if (el) el.classList.add('drag-over');
}

function presetCatDragLeave(e) {
    const el = e.target && e.target.closest ? e.target.closest('.preset-cat-label, .preset-cat-body') : null;
    if (el && e.relatedTarget && !el.contains(e.relatedTarget)) el.classList.remove('drag-over');
}

// 拖到分类标签/分类空白处：分类排序 / 同区域移字段到该分类 / 跨区域移动
async function presetCatDrop(e, zone, cat) {
    e.preventDefault();
    e.stopPropagation();
    clearPresetZoneHighlight();
    document.querySelectorAll('.preset-cat-label.drag-over, .preset-cat-body.drag-over').forEach(el => el.classList.remove('drag-over'));

    // ① 拖的是分类标签 → 调整分类顺序
    if (dragCat) {
        const srcCat = dragCat;
        dragCat = null;
        if (srcCat.zone !== zone || srcCat.cat === cat) return;
        const ordered = getOrderedCategories(zone);
        const from = ordered.indexOf(srcCat.cat);
        const to = ordered.indexOf(cat);
        if (from < 0 || to < 0) return;
        ordered.splice(from, 1);
        ordered.splice(to, 0, srcCat.cat);
        saveCatOrder(zone, ordered);
        rerenderPresetZone(zone);
        return;
    }

    // ② 拖的是预设字段
    const src = dragPreset;
    dragPreset = null;
    if (!src) return;
    if (src.zone !== zone) {
        movePresetField(src, zone, cat);
        return;
    }
    await movePresetToCategory(zone, src.key, cat);
}

/* ── 分类标签拖拽（调整分类顺序）── */
let dragCat = null; // { zone, cat }

function catDragStart(e, zone, cat) {
    dragCat = { zone, cat };
    if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', 'cat:' + cat); } catch (_) {}
    }
    const label = e.target && e.target.closest ? e.target.closest('.preset-cat-label') : null;
    if (label) label.classList.add('dragging');
}

function catDragEnd(e) {
    const label = e.target && e.target.closest ? e.target.closest('.preset-cat-label') : null;
    if (label) label.classList.remove('dragging');
    document.querySelectorAll('.preset-cat-label.drag-over, .preset-cat-body.drag-over').forEach(el => el.classList.remove('drag-over'));
    dragCat = null;
}

// 同区域内：把 srcKey 移到 targetKey 所在分类，并排在 targetKey 之后
async function reorderPresetWithinZone(zone, srcKey, targetKey) {
    const st = getPresetStore(zone);
    const list = st.presets;
    const srcIdx = list.findIndex(p => p.key === srcKey);
    let tgtIdx = list.findIndex(p => p.key === targetKey);
    if (srcIdx < 0 || tgtIdx < 0) return;

    // 重排前快照（仅持久化有变化的项）
    const before = {};
    list.forEach(p => { before[p.key] = { category: p.category, sort_order: p.sort_order }; });

    const srcItem = list[srcIdx];
    const targetCat = list[tgtIdx].category || '自定义';
    list.splice(srcIdx, 1);
    tgtIdx = list.findIndex(p => p.key === targetKey);
    list.splice(tgtIdx + 1, 0, srcItem);
    srcItem.category = targetCat;
    list.forEach((p, i) => { p.sort_order = i; });

    rerenderPresetZone(zone);
    await persistPresetOrder(zone, before);
}

// 同区域内：把 key 移到指定分类末尾
async function movePresetToCategory(zone, key, cat) {
    const st = getPresetStore(zone);
    const list = st.presets;
    const idx = list.findIndex(p => p.key === key);
    if (idx < 0) return;
    const item = list[idx];
    if ((item.category || '自定义') === cat) return; // 分类没变则不动

    const before = {};
    list.forEach(p => { before[p.key] = { category: p.category, sort_order: p.sort_order }; });

    item.category = cat;
    // 移到该分类在数组中最后一项的后面
    let lastIdx = -1;
    list.forEach((p, i) => { if (p !== item && (p.category || '自定义') === cat) lastIdx = i; });
    list.splice(idx, 1);
    if (lastIdx >= 0) {
        // lastIdx 是移除前的下标，移除后若 lastIdx > idx 需减 1
        const insertAt = (lastIdx > idx ? lastIdx - 1 : lastIdx) + 1;
        list.splice(insertAt, 0, item);
    } else {
        list.push(item);
    }
    list.forEach((p, i) => { p.sort_order = i; });

    rerenderPresetZone(zone);
    await persistPresetOrder(zone, before);
}

// 列表重渲染（保持分类筛选与展开状态、重挂打开的编辑器）
function rerenderPresetZone(zone) {
    if (zone === 'project') renderProjectPresetList();
    else if (zone === 'global') renderGlobalPresetList();
    else renderPresetList();
}

// 把变化的 category / sort_order 逐个写回后端
async function persistPresetOrder(zone, before) {
    const st = getPresetStore(zone);
    for (const p of st.presets) {
        const b = before[p.key] || {};
        const catChanged = (p.category || '自定义') !== (b.category || '自定义');
        const orderChanged = (p.sort_order || 0) !== (b.sort_order || 0);
        if (!catChanged && !orderChanged) continue;
        const body = { category: p.category || '自定义', sort_order: p.sort_order };
        try {
            if (zone === 'project') {
                if (!projectPresetState.projectId) return;
                await API.put('/api/projects/' + projectPresetState.projectId + '/presets/' + encodeURIComponent(p.key), body);
            } else if (zone === 'global') {
                await API.put('/api/project-presets/global/' + encodeURIComponent(p.key), body);
            } else {
                await API.put('/api/presets/' + encodeURIComponent(p.key), body);
            }
        } catch (e) {
            console.error('保存预设顺序失败:', p.key, e);
        }
    }
}

function presetZoneDragOver(e, zone) {
    if (!dragPreset) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    highlightPresetZone(zone);
}

function presetZoneDragLeave(e, zone) {
    const zoneEl = document.getElementById(getZoneElId(zone));
    if (zoneEl && e.relatedTarget && !zoneEl.contains(e.relatedTarget)) unhighlightPresetZone(zone);
}

function presetZoneDrop(e, zone) {
    e.preventDefault();
    const src = dragPreset;
    clearPresetZoneHighlight();
    dragPreset = null;
    if (!src || src.zone === zone) return;
    movePresetField(src, zone);
}

function getZoneElId(zone) {
    return zone === 'project' ? 'presetZoneProject'
        : (zone === 'global' ? 'presetZoneGlobal' : 'presetZoneEnterprise');
}

function highlightPresetZone(zone) {
    const el = document.getElementById(getZoneElId(zone));
    if (el) el.classList.add('drag-over');
}

function unhighlightPresetZone(zone) {
    const el = document.getElementById(getZoneElId(zone));
    if (el) el.classList.remove('drag-over');
}

function clearPresetZoneHighlight() {
    document.querySelectorAll('.preset-zone.drag-over').forEach(el => el.classList.remove('drag-over'));
}

async function movePresetField(src, targetZone, targetCat) {
    // 企业预设不支持「自动计算」类型
    if (targetZone === 'enterprise' && src.type === 'computed') {
        alert('企业预设不支持「自动计算」类型字段，无法移动。');
        return;
    }
    // 拖入项目预设需要先选定项目
    if (targetZone === 'project' && src.zone !== 'project' && !projectPresetState.projectId) {
        alert('请先在「生成报告」区域选择一个项目，再拖入项目预设。');
        return;
    }

    // 源字段完整数据（三区通用）
    const srcField = getPresetStore(src.zone).presets.find(x => x.key === src.key);
    if (!srcField) return;

    // 若编辑器正打开着被移动的字段，先关闭并丢弃其草稿
    if (inlineEditor.zone === src.zone && inlineEditor.key === src.key) {
        delete presetDrafts[src.zone][src.key];
        closeInlineEditor(false);
    }

    const key = srcField.key;
    const label = srcField.label;
    const type = srcField.type;
    const value = srcField.value || '';
    const category = targetCat || srcField.category || '自定义';
    const sortOrder = srcField.sort_order || 0;

    // 目标区是否已存在同名 key
    const targetList = getPresetStore(targetZone).presets;
    if (targetList.some(x => x.key === key)) {
        alert('目标预设区已存在字段 "' + key + '"，请先删除或重命名。');
        return;
    }

    // ★ 移动语义：先删源、后建目标，避免「先建后删」时目标区查重命中源字段
    // （如项目 shared 字段拖到「全项目通用」，建 global 时 shared 定义还在会报已存在）
    // 删除失败或重建失败时回滚：重建源字段。
    let deleted = false;
    let delRes = null;
    try {
        if (src.zone === 'enterprise') {
            delRes = await API.del('/api/presets/' + encodeURIComponent(key));
        } else if (src.zone === 'global') {
            delRes = await API.del('/api/project-presets/global/' + encodeURIComponent(key));
        } else {
            const delScope = (srcField.scope === 'shared') ? '?scope=shared' : '';
            delRes = await API.del('/api/projects/' + projectPresetState.projectId + '/presets/' + encodeURIComponent(key) + delScope);
        }
        deleted = (delRes && delRes.status === '成功');
    } catch (e) {
        alert('删除源字段失败: ' + e.message);
        return;
    }

    // 1. 创建到目标区
    try {
        let res;
        if (targetZone === 'enterprise') {
            res = await API.post('/api/presets', { key, label, type, value, category, sort_order: sortOrder });
        } else if (targetZone === 'global') {
            res = await API.post('/api/project-presets/global', { key, label, type, value, category, sort_order: sortOrder });
        } else {
            res = await API.post('/api/projects/' + projectPresetState.projectId + '/presets',
                { key, label, type, value, category, sort_order: sortOrder, scope: 'project' });
        }
        if (res.status !== '成功') {
            // 回滚：重建源字段
            await _restorePresetField(src, srcField, deleted);
            alert('移动失败: ' + (res.msg || ''));
            return;
        }
    } catch (e) {
        await _restorePresetField(src, srcField, deleted);
        alert('移动失败: ' + e.message);
        return;
    }

    // 3. 刷新三区
    loadPresets();
    loadGlobalPresets();
    if (projectPresetState.projectId) loadProjectPresets(projectPresetState.projectId);
    // 4. 刷新「可用字段」面板
    loadVariableReference(projectPresetState.projectId || undefined);
}

// 移动失败时回滚：把源字段按原 scope 重建
async function _restorePresetField(src, srcField, deleted) {
    if (!deleted) return;
    try {
        if (src.zone === 'enterprise') {
            await API.post('/api/presets', { key: srcField.key, label: srcField.label, type: srcField.type, value: srcField.value || '', category: srcField.category, sort_order: srcField.sort_order });
        } else if (src.zone === 'global') {
            await API.post('/api/project-presets/global', { key: srcField.key, label: srcField.label, type: srcField.type, value: srcField.value || '', category: srcField.category, sort_order: srcField.sort_order });
        } else {
            await API.post('/api/projects/' + projectPresetState.projectId + '/presets',
                { key: srcField.key, label: srcField.label, type: srcField.type, value: srcField.value || '', category: srcField.category, sort_order: srcField.sort_order, scope: srcField.scope || 'project' });
        }
    } catch (e) {
        console.error('回滚源字段失败:', srcField.key, e);
    }
}

/* ── 变量搜索 ── */
function filterPlaceholders(query) {
    const q = (query || '').toLowerCase();
    document.querySelectorAll('.placeholder-item').forEach(el => {
        const text = (el.getAttribute('data-search-text') || el.textContent || '').toLowerCase();
        el.style.display = (!q || text.includes(q)) ? '' : 'none';
    });
    // Hide empty categories
    document.querySelectorAll('.placeholder-category').forEach(cat => {
        const visible = cat.querySelectorAll('.placeholder-item:not([style*="display: none"])').length;
        cat.style.display = visible ? '' : 'none';
    });
}

/* ═══════════════════════════════════════════════════════════
   十年预估减排总量（生成按钮上方展示）
   ═══════════════════════════════════════════════════════════ */

// 缓存最近一次查询结果，供 VCER 阶段一表单复用
// 按项目缓存的十年预估。原来是个单槽对象，批量模式查完会把单项目的缓存冲掉。
let tenYearTotalCache = new Map();   // { projectId(number): data }

async function loadTenYearTotal(pid) {
    const el = document.getElementById('tenYearTotalHint');
    if (!el) return;
    const id = pid || document.getElementById('reportProjectSel').value;
    if (!id) {
        el.classList.add('hidden');
        return;
    }
    if (tenYearTotalCache.has(Number(id))) {
        renderTenYearTotalInto(el, tenYearTotalCache.get(Number(id)));
        return;
    }
    let data = null;
    try {
        data = await API.get(`/api/projects/${id}/report/ten-year-total`);
    } catch (e) { /* 查询失败不阻塞报告生成 */ }
    if (!data || data.status !== '成功') {
        el.classList.add('hidden');
        return;
    }
    tenYearTotalCache.set(Number(id), data);
    renderTenYearTotalInto(el, data);
}

// 把 2024-01-01 显示成 2024年01月01日（后端回传的是 ISO 格式）
function _fmtCnDate(s) {
    const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(String(s || '').trim());
    return m ? `${m[1]}年${m[2].padStart(2, '0')}月${m[3].padStart(2, '0')}日` : '';
}

// 渲染到指定元素 —— 单项目提示和批量列表都复用它，别各写一份
function renderTenYearTotalInto(el, data) {
    if (!el) return;
    const v = data && data.ten_year_total;
    if (v === null || v === undefined) {
        el.className = 'ten-year-hint is-empty';
        if (data && data.source === 'no_first_segment_data') {
            // 计入期首年段没有监测数据 —— 预估值无从算起。
            // 这里刻意不显示 0，否则会被当成「算出来就是 0」。
            const s = _fmtCnDate(data.range_start);
            const e = _fmtCnDate(data.range_end);
            const span = (s && e) ? `${s}~${e}` : '';
            el.innerHTML = '📊 十年预估减排总量：<span class="ten-year-value">无法预估</span>'
                + `<span class="ten-year-src">（计入期首年段${span ? ' ' + _esc(span) : ''}`
                + '没有监测数据。预估值取自首年段的实测减排量，请先补录该时段的监测数据，'
                + '或修正项目的基准日期）</span>';
        } else {
            el.innerHTML = '📊 十年预估减排总量：<span class="ten-year-value">暂无数据</span>'
                + '<span class="ten-year-src">（需先填写「预估年减排量」或补充监测数据）</span>';
        }
        return;
    }
    const num = Number(v);
    // 说明口径：是哪个区间算出来的 / 是哪份报告固化的。
    // 两个来源的数可能差异很大（实时估算按近 30 天折算，已固化的按当初报告区间算），
    // 不写清楚区间会被当成"同一个数怎么变了"。
    const rs = _fmtCnDate(data.range_start);
    const re = _fmtCnDate(data.range_end);
    let note = '';
    if (data.source === 'latest_pdd') {
        const day = data.generated_at ? data.generated_at.slice(0, 10) : '';
        note = day ? `${day} 生成的 PDD 中的值` : 'PDD 中的值';
        if (rs && re) note += `，报告区间 ${rs}~${re}`;
    } else if (data.source === 'computed' && rs && re) {
        note = `按 ${rs}~${re} 的监测数据折算`;
    } else if (data.source_label) {
        note = data.source_label;
    }
    el.className = 'ten-year-hint' + (num ? '' : ' is-empty');
    el.innerHTML = `📊 十年预估减排总量：<span class="ten-year-value">${num.toFixed(2)}</span> tCO₂e`
        + (note ? `<span class="ten-year-src">（${_esc(note)}）</span>` : '');
}

function renderTenYearTotal(data) {
    renderTenYearTotalInto(document.getElementById('tenYearTotalHint'), data);
}

// 生成报告后立即刷新（尤其是刚生成的 PDD）
function invalidateTenYearTotal() {
    tenYearTotalCache.clear();
}

function openReportForProject(projectId) {
    switchTab('reports');
    const sel = document.getElementById('reportProjectSel');
    sel.value = String(projectId);
    CustomSelect.refresh(sel);
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    document.querySelector('#tab-reports .report-card-generate')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function generateReport(projectId) {
    const sel = document.getElementById('reportProjectSel');
    if (projectId) {
        sel.value = String(projectId);
        CustomSelect.refresh(sel);
        sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const pid = parseInt(sel.value, 10);
    const statusEl = document.getElementById('reportGenStatus');
    const btn = document.getElementById('btnGenerateReport');
    if (!Number.isFinite(pid)) { alert('请先选择项目'); return; }

    const type = document.getElementById('reportTypeSel').value;
    const startDate = document.getElementById('reportStartDate').value;
    const endDate = document.getElementById('reportEndDate').value;
    const templateSel = document.getElementById('reportTemplateSel');
    const template = templateSel ? templateSel.value : '';
    const fnEl = document.getElementById('reportFilename');
    const filenameTemplate = fnEl && fnEl.value.trim() ? fnEl.value.trim() : '';

    if (!document.getElementById('tab-reports').classList.contains('active')) {
        switchTab('reports');
    }
    statusEl.classList.remove('hidden', 'hiding');
    statusEl.className = 'status-msg';
    statusEl.textContent = '正在生成报告...';
    if (btn) btn.disabled = true;

    const payload = { report_type: type };
    if (template) payload.template = template;
    if (startDate) payload.start_date = startDate;
    if (endDate) payload.end_date = endDate;
    if (filenameTemplate) payload.filename_template = filenameTemplate;

    try {
        await _generateSingle(pid, payload, statusEl);
    } catch (e) {
        statusEl.className = 'status-msg error';
        statusEl.textContent = '网络错误: ' + e.message;
    } finally {
        if (btn) btn.disabled = false;
        updateReportGate();
    }
}

// 单份：调单份接口，直接下这一个文件
async function confirmContinuity(pid,startDate,endDate) {
    const scope=await API.post(`/api/projects/${pid}/monitoring/continuity/preflight`,
                              {start_date:startDate||null,end_date:endDate||null});
    if (scope.status!=='成功') throw new Error(scope.msg||'计量区间检查失败');
    if (!scope.requires_confirmation) return scope.signature;
    return new Promise(resolve=>{
        const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.style.zIndex='1800';
        const fmt=v=>v==null?'—':UI.fmtNum(v,2);
        const details=scope.warnings.map(w=>`<section class="continuity-warning"><b>${_esc(w.machine_name)}</b>
          <p>${_esc(w.start)} — ${_esc(w.end)}</p>
          <p>前一阶段 / B：${_esc(w.before?.timestamp||'无边界读数')}；产气 ${fmt(w.before?.flow)} m³，电量 ${fmt(w.before?.power)} kWh；阶段减排 ${fmt(w.before_reduction)} tCO₂e。</p>
          <p>后一读数 / C：${_esc(w.after?.timestamp||w.end)}；产气 ${fmt(w.after?.flow)} m³，电量 ${fmt(w.after?.power)} kWh。</p>
          <p>采用方式：${_esc(w.calculation)}。${_esc(w.probe?.summary||'尚无可证明中途连续性的回查记录。')}${(w.probe?.chunks||[]).some(c=>c.status==='error')?' 回查仍有失败时间块，结果不完整。':''}</p></section>`).join('');
        overlay.innerHTML=`<div class="modal-card" style="max-width:780px"><div class="modal-header"><span>核算期数据提示</span></div>
          <div class="modal-body" style="max-height:65vh;overflow:auto"><p>本区间存在未确认缺口或计量阶段边界，结果可能不准确。请核对以下信息，确认后可继续。</p>${details}</div>
          <div class="modal-footer"><button class="btn btn-outline" data-action="cancel">取消</button><button class="btn btn-primary" data-action="continue">已知悉，继续</button></div></div>`;
        const finish=value=>{overlay.remove();resolve(value);};
        overlay.querySelector('[data-action="cancel"]').onclick=()=>finish(null);
        overlay.querySelector('[data-action="continue"]').onclick=()=>finish(scope.signature);
        document.body.appendChild(overlay);
    });
}

async function _generateSingle(pid, payload, statusEl) {
    const ack=await confirmContinuity(pid,payload.start_date,payload.end_date);
    if (ack===null) {statusEl.textContent='已取消生成';return;}
    payload.continuity_ack=ack;
    const data = await API.post(`/api/projects/${pid}/report/generate`, payload);
    if (data.status !== '成功') {
        statusEl.className = 'status-msg error';
        statusEl.textContent = '生成失败: ' + (data.msg || '未知错误');
        return;
    }
    statusEl.className = 'status-msg success';
    let msg = '✅ 报告生成成功，已自动开始下载：'
        + `<a href="${data.download_url}" onclick="App.downloadReport(this.href); return false;">若未弹出请点此下载</a>`;
    // PDD 生成后直接把「十年预估减排总量」写出来，省得再点开报告翻表 B.4
    if (data.ten_year_ready === false) {
        msg += '<br>⚠ <strong>十年预估减排总量无法计算</strong>：该项目计入期首年段没有监测数据，'
             + '报告里三张预估表会是空的。请先补录首年段的监测数据再生成。';
    } else if (data.ten_year_total != null) {
        msg += `<br>📊 十年预估减排总量：<strong>${Number(data.ten_year_total).toFixed(2)}</strong> tCO₂e`;
    }
    if ((data.quality_cases||[]).length) msg += '<div class="report-notice">本次已确认核算期的数据提示；确认记录已保留。</div>';
    msg += REPORT_CHECK_NOTICE_HTML;
    statusEl.innerHTML = msg;
    autoDownload(data.download_url);
    invalidateTenYearTotal();
    loadTenYearTotal(pid);
    loadReportHistory();
}

// 生成完成后统一提示：报告要人工过一遍再盖章上传
const REPORT_CHECK_NOTICE_HTML =
    '<div class="report-notice">⚠ 请检查生成的报告有无问题，确认无误后盖章，再上传申请。</div>';

// 先检查 HTTP 状态和报文，避免把缺失报告的 JSON 错误保存成附件。
async function autoDownload(url) {
    if (!url) return false;
    try {
        const response = await fetch(url);
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('json')) {
            const result = await response.json();
            throw new Error(result.msg || '报告下载失败');
        }
        if (!response.ok) throw new Error(`报告下载失败（HTTP ${response.status}）`);
        const disposition = response.headers.get('content-disposition') || '';
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const plain = disposition.match(/filename="([^"]+)"|filename=([^;]+)/i);
        let filename = '报告.docx';
        if (encoded) filename = decodeURIComponent(encoded[1]);
        else if (plain) filename = (plain[1] || plain[2]).trim();
        const blobUrl = URL.createObjectURL(await response.blob());
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
        return true;
    } catch (e) {
        UI.toast(e.message || '报告下载失败', 'error');
        return false;
    }
}

// 当选择项目时，刷新占位符示例值
function onReportProjectChange() {
    const pid = document.getElementById('reportProjectSel').value;
    if (pid) loadVariableReference(parseInt(pid));
    else loadVariableReference();
    updateReportGate();
    loadTenYearTotal(pid);
}

// 阶段已通过时禁用对应类型的报告生成，后端会再次校验。
function updateReportGate() {
    const btn = document.getElementById('btnGenerateReport');
    if (!btn) return;
    const id = parseInt((document.getElementById('reportProjectSel') || {}).value, 10);
    const type = document.getElementById('reportTypeSel').value;
    const stage = type === 'pdd' ? 1 : (type === 'approval' ? 2 : 0);
    const project = state.projects.find(p => p.id === id);
    const passed = !!(project && stage && project['stage' + stage + '_passed']);
    const blocked = passed && !state.devMode;
    const msg = passed
        ? (state.devMode
            ? `第${stage}阶段已通过（开发者模式已解禁，仍可生成）`
            : `第${stage}阶段已通过，禁止生成此报告（可在设置中开启「开发者选项」解禁）`)
        : '';
    btn.disabled = blocked;
    btn.title = msg;
    btn.style.opacity = blocked ? '0.5' : '';
    btn.style.cursor = blocked ? 'not-allowed' : '';
}

// 折叠卡片（带高度动画）
function toggleCollapsible(headerEl) {
    const card = headerEl.closest('.collapsible-card');
    const body = card ? card.querySelector('.collapsible-body') : null;
    if (!card || !body) {
        if (card) card.classList.toggle('collapsed');
        return;
    }
    if (card.classList.contains('collapsed')) {
        // 展开：先移除 class 恢复 display，再从 0 动画展开
        card.classList.remove('collapsed');
        animateMaxHeight(body, true);
    } else {
        // 收起：先动画到 0，完成后再加 class（display:none）
        animateMaxHeight(body, false, () => card.classList.add('collapsed'));
    }
}

// 通用高度动画：expand=true 从 0 展开到实际高度，false 收起为 0
function animateMaxHeight(el, expand, done) {
    if (expand) {
        el.style.maxHeight = '0px';
        el.style.opacity = '0';
        requestAnimationFrame(() => requestAnimationFrame(() => {
            el.style.maxHeight = el.scrollHeight + 'px';
            el.style.opacity = '1';
        }));
        setTimeout(() => {
            el.style.maxHeight = 'none';
            if (done) done();
        }, 340);
    } else {
        el.style.maxHeight = el.scrollHeight + 'px';
        requestAnimationFrame(() => requestAnimationFrame(() => {
            el.style.maxHeight = '0px';
            el.style.opacity = '0';
        }));
        setTimeout(() => { if (done) done(); }, 340);
    }
}



// ═══════════════════════════════════════════════════════════
// VCER 企业自助申请（完整5阶段生命周期管理）
// 业务规则:
//   阶段1(申请)、阶段2(审定) — 一次性，通过后不可重提
//   阶段3(核查)、阶段4(交易)、阶段5(抵消) — 可多轮
//   交易轮数 ≤ 核查轮数，抵消轮数 ≤ 核查轮数
// ═══════════════════════════════════════════════════════════

const VCER_STAGE_NAMES = {1:'项目申请', 2:'项目审定', 3:'项目核查', 4:'项目交易', 5:'碳减排量抵消'};
const VCER_STAGE_DESC = {
    1: '提交项目申请书和设计文件，申请 VCER 项目登记。每个项目仅可申请一次。',
    2: '项目申请通过后，上传合同进行审定。每个项目仅可审定一次。',
    3: '提交碳减排量核算报告，支持多轮核查。交易和抵消按持有余额判断，不按核查轮数限制。',
    4: '提交交易合同和证书。项目可用碳资产（净）足够时可继续交易，提交后由促进会校验项目余额。',
    5: '提交抵消声明。抵消从买方在该项目的持有余额扣除，由促进会校验；来源项目会记录这笔抵消。',
};

// VCER 模块内部状态
const _V = {
    projectId: null,         // 当前项目 ID
    vcerData: null,          // VCER 平台返回的完整数据
    stages: [],              // [{stage, name, status, reason}] — 5阶段状态
    currentStage: 0,         // 当前选中的阶段 1-5
    selectedRound: null,     // 当前选中的轮次号
    rounds: [],              // 当前阶段的轮次列表
    roundCounts: {3:-1,4:-1,5:-1}, // 已通过轮数，-1=未加载
    roundsLoaded: false,     // 轮次数据是否加载完毕
    roundsFetched: false,    // 本次状态是否已并行拉取过轮次（防重复触发）
    roundsCache: {},         // 各阶段完整轮次缓存 {3:[...],4:[...],5:[...]}
    queryKey: '',            // 查询密钥
    queryPassword: '',       // 查询密码（从本地DB取）
    projectName: '',         // 项目名称
};

/* ── 工具函数 ── */
function _parseReasons(raw) {
    // 解析驳回原因 JSON 数组 → 可读文本（带轮次编号）
    if (!raw) return '';
    let arr;
    if (typeof raw === 'string' && raw.trim().startsWith('[')) {
        try { arr = JSON.parse(raw); } catch(e) { arr = [raw]; }
    } else if (Array.isArray(raw)) { arr = raw; }
    else { return String(raw); }

    if (arr.length <= 1) return arr[0] || '';
    // 多轮驳回：每轮一行，带编号
    const cnOrd = ['一','二','三','四','五','六','七','八','九','十'];
    return arr.map((r, i) => `第${cnOrd[i] || (i+1)}次驳回：${r}`).join('<br>');
}

function _isOneTimeStage(s) { return s === 1 || s === 2; }
function _isMultiRoundStage(s) { return s === 3 || s === 4 || s === 5; }

/* ── Tab 初始化 ── */
async function loadVCERTab() {
    const sel = document.getElementById('vcerProjectSel');
    const prevVal = sel.value;
    sel.innerHTML = '<option value="">-- 选择项目 --</option>';
    if (!state.projects.length) {
        try { const data = await API.get('/api/projects'); state.projects = data.projects || []; } catch(e) {}
    }
    state.projects.filter(p => p.lifecycle_status === 'active').forEach(p => {
        sel.innerHTML += `<option value="${p.id}" data-search-text="${_esc(p.project_name + " " + (p.project_code || ""))}">${_esc(p.project_name)}</option>`;
    });
    sel.value = prevVal;
    CustomSelect.refresh(sel);

    sel.onchange = async () => {
        if (sel.value) {
            _V.projectId = parseInt(sel.value);
            // 获取本地存储的密码
            const p = state.projects.find(x => x.id == _V.projectId);
            _V.queryPassword = (p && p.vcer_query_pwd) ? p.vcer_query_pwd : '';
            _V.queryKey = (p && p.vcer_query_key) ? p.vcer_query_key : '';
            await refreshVCERStatus(_V.projectId);
        } else {
            _resetVCERUI();
        }
    };

    if (sel.value) {
        _V.projectId = parseInt(sel.value);
        const p = state.projects.find(x => x.id == _V.projectId);
        _V.queryPassword = (p && p.vcer_query_pwd) ? p.vcer_query_pwd : '';
        _V.queryKey = (p && p.vcer_query_key) ? p.vcer_query_key : '';
        await refreshVCERStatus(_V.projectId);
    } else {
        _resetVCERUI();
    }
}

function _resetVCERUI() {
    _V.projectId = null; _V.vcerData = null; _V.stages = []; _V.currentStage = 0;
    _V.selectedRound = null; _V.rounds = []; _V.roundCounts = {3:-1,4:-1,5:-1}; _V.roundsLoaded = false; _V.roundsFetched = false; _V.roundsCache = {};
    document.getElementById('vcerStageBar').innerHTML = '<p class="muted">请选择项目查看审核进度</p>';
    document.getElementById('vcerFormBody').innerHTML = '<p class="muted">请选择项目后操作</p>';
    document.getElementById('vcerFormTitle').innerHTML = '📝 提交材料';
    document.getElementById('vcerRoundStack').innerHTML = '';
    document.getElementById('vcerRoundDetail').innerHTML = '<p class="muted">选择阶段查看提交历史</p>';
    document.getElementById('vcerHistoryStageLabel').textContent = '';
    document.getElementById('btnVcerNewRound').classList.add('hidden');
    document.getElementById('vcerRoundConstraint').classList.add('hidden');
    // 遮罩层用 style 确保隐藏
    const overlay = document.getElementById('vcerStageOverlay');
    overlay.classList.add('hidden');
    overlay.style.display = 'none';
    document.getElementById('vcerKeyBadge').style.display = 'none';
    document.getElementById('vcerInfoBar').classList.add('hidden');
    document.getElementById('vcerAllDone').classList.add('hidden');
}

/* ── 刷新 VCER 状态：核心入口 ── */
async function refreshVCERStatus(projectId) {
    const pid = projectId || _V.projectId;
    if (!pid) return;
    _V.projectId = parseInt(pid);

    const barEl = document.getElementById('vcerStageBar');
    barEl.innerHTML = '<p class="muted">加载中...</p>';

    try {
        const resp = await API.get(`/api/projects/${pid}/vcer/status`);
        if (resp.status !== '成功') {
            barEl.innerHTML = `<p class="muted">${resp.msg || '查询失败'}</p>`;
            _V.stages = [];
            // 未关联密钥 → 显示阶段1表单
            if (resp.msg && resp.msg.indexOf('未关联') >= 0) {
                _V.currentStage = 1;
                _renderStageForm(1);
                _showOverlay(null);
            }
            return;
        }

        // 保存状态
        _V.vcerData = resp;
        _V.stages = resp.stages || [];
        _V.queryKey = resp.query_key || _V.queryKey;
        _V.adminBaseUrl = resp.admin_base_url || '';
        if (resp.project_code) { _V.projectName = resp.project_code; }

        // 重置轮次计数，等待异步加载
        _V.roundCounts = {3:-1, 4:-1, 5:-1};
        _V.roundsLoaded = false;
        _V.roundsFetched = false;
        _V.roundsCache = {};

        // 本地更新该项目在 state.projects 里的通过状态，避免整表重拉
        const projInState = state.projects.find(x => x.id === _V.projectId);
        if (projInState && _V.stages.length) {
            _V.stages.forEach(s => {
                projInState[`stage${s.stage}_passed`] = (s.status === '已通过') ? 1 : 0;
            });
        }

        // 更新 UI
        _renderStageBar();
        _updateInfoBar();

        // 确定默认显示哪个阶段
        _autoSelectStage();

        // 如全部通过，显示横幅（必须有项目且 stages 非空）
        if (_V.projectId && _V.stages.length === 5 && _V.stages.every(s => s.status === '已通过')) {
            document.getElementById('vcerAllDone').classList.remove('hidden');
            document.getElementById('vcerDoneProjectName').textContent =
                `项目编号: ${resp.project_code || ''}  |  密钥: ${_V.queryKey}`;
        } else {
            document.getElementById('vcerAllDone').classList.add('hidden');
        }

    } catch(e) {
        barEl.innerHTML = `<p class="muted">查询失败: ${e.message}</p>`;
    }
}

/* ── 渲染5阶段进度条（企业风格） ── */
function _renderStageBar() {
    const barEl = document.getElementById('vcerStageBar');
    if (!_V.stages.length) {
        barEl.innerHTML = '<p class="muted">暂无数据</p>';
        return;
    }

    // 先异步获取轮次数（阶段3/4/5）
    _fetchRoundCounts();

    let html = '<div class="vcer-stage-bar-enterprise">';
    _V.stages.forEach((s, i) => {
        let cls = 'pending';
        if (s.status === '已通过') cls = 'passed';
        else if (s.status === '待审核') cls = 'review';
        else if (s.status === '已驳回') cls = 'rejected';
        else if (s.status === '申请修改中') cls = 'editing';

        const isActive = _V.currentStage === s.stage;
        const activeCls = isActive ? ' active' : '';

        // 轮次计数徽章（仅多轮阶段且数据已加载、有轮次时显示）
        let roundBadge = '';
        const cnt = _V.roundCounts[s.stage];
        if (_isMultiRoundStage(s.stage) && cnt > 0) {
            roundBadge = `<span class="vcer-round-count">×${cnt}</span>`;
        }

        // 驳回时 tooltip
        const reasonTitle = (s.status === '已驳回' && s.reason)
            ? ` title="${_parseReasons(s.reason).replace(/"/g, '&quot;')}"` : '';

        html += `<div class="vcer-stage-node ${cls}${activeCls}" data-stage="${s.stage}"
            onclick="App.vcerSelectStage(${s.stage})"${reasonTitle}>
            <span class="stage-num">${s.stage}</span>
            <span class="stage-name">${s.name}${roundBadge}</span>
            <span class="stage-badge">${s.status}</span>
        </div>`;

        if (i < _V.stages.length - 1) {
            html += '<span class="vcer-stage-arrow">▸</span>';
        }
    });
    html += '</div>';

    barEl.innerHTML = html;
}

async function _fetchRoundCounts() {
    if (_V.roundsFetched) return;  // 本次状态已并行拉取过轮次，避免重复触发
    _V.roundsFetched = true;
    let changed = false;
    const results = await Promise.all([3, 4, 5].map(async (stage) => {
        try {
            const rounds = await _fetchRoundsDirect(stage);
            _V.roundsCache[stage] = rounds;
            const passed = rounds.filter(r => r.status === '已通过');
            return { stage, count: passed.length };
        } catch(e) {}
        return { stage, count: _V.roundCounts[stage] };
    }));
    for (const { stage, count } of results) {
        if (_V.roundCounts[stage] !== count) {
            _V.roundCounts[stage] = count;
            changed = true;
        }
    }
    _V.roundsLoaded = true;
    // 轮次有变化则重绘（重绘内因 roundsFetched 已置位，不会再拉取）
    if (changed) _renderStageBar();
}

/* ── 项目信息栏 ── */
function _updateInfoBar() {
    const bar = document.getElementById('vcerInfoBar');
    const badge = document.getElementById('vcerKeyBadge');
    const keyDisplay = document.getElementById('vcerKeyDisplay');

    if (_V.queryKey) {
        bar.classList.remove('hidden');
        document.getElementById('vcerInfoText').innerHTML =
            `<strong>${_V.vcerData.enterprise_name || ''}</strong> — ${_V.vcerData.project_code || _V.projectName || ''}`;
        badge.style.display = '';
        keyDisplay.textContent = _V.queryKey;
    } else {
        bar.classList.add('hidden');
        badge.style.display = 'none';
    }
}

/* ── 自动选择操作阶段 ── */
function _autoSelectStage() {
    // 优先级: 已驳回 > 待提交 > 申请修改中 > 待审核 > 全部通过选阶段5
    const rejected = _V.stages.find(s => s.status === '已驳回');
    const waitingSubmit = _V.stages.find(s => s.status === '待提交');
    const editing = _V.stages.find(s => s.status === '申请修改中');
    const pending = _V.stages.find(s => s.status === '待审核');

    let target;
    if (rejected) target = rejected;
    else if (waitingSubmit) target = waitingSubmit;
    else if (editing) target = editing;
    else if (pending) target = pending;
    else if (_V.stages.every(s => s.status === '已通过')) target = _V.stages[4]; // 阶段5

    if (target) {
        _V.currentStage = target.stage;
        _selectStage(target.stage);
    }
}

/* ── 阶段切换 ── */
function vcerSelectStage(stage) {
    if (!_V.projectId) return;
    _V.currentStage = stage;
    _selectStage(stage);
    // 更新进度条高亮
    _renderStageBar();
}

function _selectStage(stage) {
    _V.selectedRound = null;
    _renderStageForm(stage);
    _loadRounds(stage);
}

/* ── 渲染阶段表单 + 遮罩层 ── */
function _renderStageForm(stage) {
    const titleEl = document.getElementById('vcerFormTitle');
    titleEl.innerHTML = `📝 阶段${stage}：${VCER_STAGE_NAMES[stage]}`;

    const stageData = _V.stages.find(s => s.stage === stage);
    const status = stageData ? stageData.status : '未提交';

    // 判断是否显示遮罩层
    let overlayType = null;
    if (_isOneTimeStage(stage)) {
        if (status === '已通过') overlayType = 'passed';
        else if (status === '待审核') overlayType = 'review';
        else if (status === '已驳回') overlayType = 'rejected';
        else if (status === '申请修改中') overlayType = 'editing';
    } else {
        // 多轮阶段：待审核或驳回时显示遮罩，已通过时允许新增
        if (status === '待审核') overlayType = 'review';
        else if (status === '已驳回') overlayType = 'rejected';
        else if (status === '申请修改中') overlayType = 'editing';
        // 已通过 / 未提交 → 不显示遮罩，可填表
    }

    _showOverlay(overlayType, stage, stageData);

    // 根据 overlayType 决定表单区域显示什么
    if (overlayType === 'passed' && _isOneTimeStage(stage)) {
        document.getElementById('vcerFormBody').innerHTML = '';
    } else if (overlayType === 'review') {
        document.getElementById('vcerFormBody').innerHTML = '';
    } else if (!overlayType && _isMultiRoundStage(stage) && status === '已通过') {
        // 多轮阶段已通过：提示用户点击"新增一轮"，不自动展示表单
        document.getElementById('vcerFormBody').innerHTML =
            '<p class="muted" style="text-align:center;padding:40px 0">✅ 已完成<br><br>'
            + '如需提交新一轮，请点击右侧<b>「➕ 新增一轮」</b>按钮</p>';
    } else {
        _renderFormHTML(stage);
    }
}

function _showOverlay(type, stage, stageData) {
    const overlay = document.getElementById('vcerStageOverlay');
    if (!type) {
        overlay.classList.add('hidden');
        overlay.style.display = 'none';  // 确保隐藏
        return;
    }

    overlay.style.display = '';  // 清除 inline display
    overlay.classList.remove('hidden', 'passed', 'review', 'rejected', 'editing');
    overlay.classList.add(type);

    const icon = document.getElementById('vcerOverlayIcon');
    const msg = document.getElementById('vcerOverlayMsg');
    const detail = document.getElementById('vcerOverlayDetail');
    const btn = document.getElementById('vcerOverlayBtn');

    detail.classList.add('hidden');
    btn.classList.add('hidden');

    switch(type) {
        case 'passed':
            icon.textContent = '✅';
            msg.textContent = `阶段${stage}：${VCER_STAGE_NAMES[stage]} 已通过`;
            if (_isOneTimeStage(stage)) {
                // 一次性阶段通过后显示简要信息
                detail.classList.remove('hidden');
                detail.innerHTML = '此阶段已完成，无需再次提交。';
            }
            break;
        case 'review':
            icon.textContent = '⏳';
            msg.textContent = `阶段${stage}：${VCER_STAGE_NAMES[stage]} 审核中`;
            detail.classList.remove('hidden');
            detail.innerHTML = '材料已提交，请耐心等待管理员审核。';
            break;
        case 'rejected':
            icon.textContent = '❌';
            msg.textContent = `阶段${stage}：${VCER_STAGE_NAMES[stage]} 已驳回`;
            if (stageData && stageData.reason) {
                detail.classList.remove('hidden');
                const reasons = _parseReasons(stageData.reason);
                detail.innerHTML = `<strong>驳回原因：</strong><br>${reasons}`;
            }
            btn.style.display = '';
            btn.textContent = '📝 修改材料重提';
            btn.onclick = function() {
                try {
                    _showOverlay(null);
                    _renderFormHTML(stage);
                    if (_isMultiRoundStage(stage)) _loadRounds(stage);
                } catch(e) {
                    console.error('修改材料重提失败:', e);
                    alert('操作失败: ' + e.message);
                }
            };
            break;
        case 'editing':
            icon.textContent = '📝';
            msg.textContent = '申请修改中，等待管理员确认';
            break;
    }
}

/* ── VCER 阶段一：十年预估减排总量取值来源（最新 PDD / 手动）── */
const vcerTenYear = { projectId: null, pddValue: '', touched: false };

function _vcerTenYearInit(projectId) {
    vcerTenYear.projectId = String(projectId);
    vcerTenYear.pddValue = '';
    vcerTenYear.touched = false;

    const apply = (data) => {
        // 项目已切换（用户关了弹窗又打开另一个项目）时丢弃过期响应
        if (vcerTenYear.projectId !== String(projectId)) return;
        if (!data || data.status !== '成功' || data.ten_year_total == null) return;
        vcerTenYear.pddValue = Number(data.ten_year_total).toFixed(2);
        const sel = document.getElementById('vcerTenYearSource');
        if (sel && !vcerTenYear.touched) {
            sel.value = 'pdd';
            CustomSelect.refresh(sel);   // 自定义下拉要手动同步触发器文字
        }
        _vcerTenYearSync();
    };

    // 报告中心缓存过就先填上，避免闪一下空白
    if (tenYearTotalCache.has(Number(projectId))) {
        apply(tenYearTotalCache.get(Number(projectId)));
    }
    API.get(`/api/projects/${projectId}/report/ten-year-total`).then(d => {
        if (d && d.status === '成功') {
            tenYearTotalCache.set(Number(projectId), d);
            apply(d);
        }
    }).catch(() => { /* 取不到就保持手动填写 */ });
}

function _vcerTenYearSync() {
    const sel = document.getElementById('vcerTenYearSource');
    const input = document.getElementById('vcerTenYearInput');
    const hint = document.getElementById('vcerTenYearHint');
    if (!sel || !input) return;

    if (sel.value === 'pdd' && vcerTenYear.pddValue) {
        input.value = vcerTenYear.pddValue;
        input.readOnly = true;
        if (hint) hint.textContent = '✅ 已填入该项目最新生成的 PDD 中的预估值，提交后固化。';
        return;
    }
    input.readOnly = false;
    if (hint) {
        hint.textContent = sel.value === 'pdd'
            ? '⚠ 该项目还没有可用的 PDD 预估值，请先生成 PDD，或改为手动填写。'
            : '✍ 请手动填写 PDD 报告中的「预计的温室气体总减排量」。';
    }
}

function onVcerTenYearSourceChange() {
    vcerTenYear.touched = true;
    _vcerTenYearSync();
}

/* ── 渲染阶段表单 HTML ── */
function _renderFormHTML(stage) {
    const bodyEl = document.getElementById('vcerFormBody');
    const proj = state.projects.find(p => p.id == _V.projectId) || {};
    const qk = _V.queryKey || proj.vcer_query_key || '';
    const pwd = _V.queryPassword || proj.vcer_query_pwd || '';
    // 十年预估减排总量预填：已固化值优先，否则按预估年减排量×10
    const tenYearPre = proj.pending_validation_10yr != null && proj.pending_validation_10yr !== ''
        ? proj.pending_validation_10yr
        : (proj.estimated_annual_reduction != null && proj.estimated_annual_reduction !== ''
            ? (parseFloat(proj.estimated_annual_reduction) * 10) : '');

    // 异步预填阶段1企业信息
    if (stage === 1) {
        (async () => {
            try {
                const cfg = await API.get('/api/config');
                const el = document.getElementById('vcerEntName');
                if (el && !el.value) el.value = cfg.enterprise_name || '';
            } catch(e) {}
        })();
    }

    let html = `<p style="color:var(--text-gray);margin-bottom:14px;font-size:13px">${VCER_STAGE_DESC[stage]}</p>
        <form id="vcerForm" enctype="multipart/form-data" onsubmit="return false;">
        <input type="hidden" name="stage" value="${stage}">`;

    if (stage === 1) {
        html += `
            <div class="form-group"><label>企业名称 <span style="color:var(--danger)">*</span></label>
                <input type="text" name="enterprise_name" class="input" id="vcerEntName" required></div>
            <div class="form-group"><label>项目名称 <span style="color:var(--danger)">*</span></label>
                <input type="text" name="project_name" class="input" id="vcerProjName" value="${_esc(proj.project_name)}" required></div>
            <div class="form-group"><label>项目类型</label>
                <input type="text" name="project_type" class="input" id="vcerProjType" value="${_esc(proj.project_type || proj.hook_name || '')}"></div>
            <div class="form-group"><label>项目简介</label>
                <textarea name="project_summary" class="input" rows="2" id="vcerSummary">${_esc(proj.project_summary || '')}</textarea></div>
            <div class="form-group"><label>联系人</label>
                <input type="text" name="contact_person" class="input"></div>
            <div class="form-group"><label>联系电话</label>
                <input type="text" name="contact_phone" class="input"></div>
            <div class="form-group"><label>查询密码 <span style="color:var(--danger)">*</span> <small>(用于后续查询和提交，请妥善保管)</small></label>
                <input type="password" name="query_password" class="input" placeholder="至少4位" required></div>
            <div class="form-group"><label>十年预估减排总量取值方式</label>
                <select name="ten_year_source" id="vcerTenYearSource" class="input custom-select"
                        data-placeholder="选择取值方式" onchange="onVcerTenYearSourceChange()">
                    <option value="pdd">使用最新生成的 PDD 中的预估值</option>
                    <option value="manual">手动填写</option>
                </select></div>
            <div class="form-group"><label>十年预估减排总量 (tCO₂e) <small>（提交后固化，须与 PDD 一致）</small></label>
                <input type="number" name="ten_year_total" class="input" id="vcerTenYearInput"
                       step="0.0001" value="${tenYearPre}" placeholder="如 3289.1">
                <small class="muted" id="vcerTenYearHint"></small></div>
            <div class="form-group"><label>申请书 (.pdf) <span style="color:var(--danger)">*</span></label>
                <input type="file" name="application_form" class="input" accept=".pdf"></div>
            <div class="form-group"><label>项目设计文件 (.docx/.doc) <span style="color:var(--danger)">*</span></label>
                <input type="file" name="design_doc" class="input" accept=".docx,.doc"></div>`;
    } else if (stage === 2) {
        html += `
            <input type="hidden" name="query_key" value="${_esc(qk)}">
            <div class="form-group"><label>查询密码 <span style="color:var(--danger)">*</span></label>
                <input type="password" name="query_password" class="input" value="${_esc(pwd)}" placeholder="阶段1设置的密码"></div>
            <div class="form-group"><label>合同 (.pdf) <span style="color:var(--danger)">*</span></label>
                <input type="file" name="contract" class="input" accept=".pdf"></div>`;
    } else if (stage === 3) {
        html += `
            <input type="hidden" name="query_key" value="${_esc(qk)}">
            <div class="form-group"><label>查询密码 <span style="color:var(--danger)">*</span></label>
                <input type="password" name="query_password" class="input" value="${_esc(pwd)}" placeholder="阶段1设置的密码"></div>
            <div class="machine-form-grid"><div class="form-group"><label>本轮核查起始日期（本地检查用）</label><input type="date" name="verification_start" class="input" required></div>
            <div class="form-group"><label>本轮核查截止日期（本地检查用）</label><input type="date" name="verification_end" class="input" required></div></div>
            <div class="form-group"><label>核证减排量 (tCO₂e)</label>
                <input type="number" name="total_reduction" class="input" step="0.0001" placeholder="如 100.50"></div>
            <div class="form-group"><label>碳减排量核算报告 (.pdf) <span style="color:var(--danger)">*</span></label>
                <input type="file" name="verification_report" class="input" accept=".pdf"></div>`;
    } else if (stage === 4) {
        html += `
            <input type="hidden" name="query_key" value="${_esc(qk)}">
            <div class="form-group"><label>查询密码 <span style="color:var(--danger)">*</span></label>
                <input type="password" name="query_password" class="input" value="${_esc(pwd)}" placeholder="阶段1设置的密码"></div>
            <div class="form-group"><label>买方名称 <span style="color:var(--danger)">*</span></label>
                <input type="text" name="buyer_name" class="input"></div>
            <div class="form-group"><label>卖方名称 <span style="color:var(--danger)">*</span></label>
                <input type="text" name="seller_name" class="input"></div>
            <div class="form-group"><label>交易量 (tCO₂e) <span style="color:var(--danger)">*</span></label>
                <input type="number" name="traded_amount" class="input" step="0.0001" placeholder="如 50"></div>
            <div class="form-group"><label>合同 (.pdf) <span style="color:var(--danger)">*</span></label>
                <input type="file" name="contract" class="input" accept=".pdf"></div>
            <div class="form-group"><label>碳减排量核证证书 <span style="color:var(--danger)">*</span></label>
                <input type="file" name="certificate" class="input" accept=".pdf,.jpg,.png"></div>`;
    } else if (stage === 5) {
        html += `
            <input type="hidden" name="query_key" value="${_esc(qk)}">
            <div class="form-group"><label>查询密码 <span style="color:var(--danger)">*</span></label>
                <input type="password" name="query_password" class="input" value="${_esc(pwd)}" placeholder="阶段1设置的密码"></div>
            <div class="form-group"><label>买方/抵消方名称 <span style="color:var(--danger)">*</span></label>
                <input type="text" name="buyer_name" class="input"></div>
            <div class="form-group"><label>抵消量 (tCO₂e)</label>
                <input type="number" name="offset_amount" class="input" step="0.0001" placeholder="如 50"></div>
            <div class="form-group"><label>抵消声明 (.pdf) <span style="color:var(--danger)">*</span></label>
                <input type="file" name="offset_declaration" class="input" accept=".pdf"></div>
            <div class="form-group"><label>碳减排量核证证书 <span style="color:var(--danger)">*</span></label>
                <input type="file" name="certificate" class="input" accept=".pdf,.jpg,.png"></div>`;
    }

    html += `<button class="btn btn-primary" style="margin-top:12px;width:100%" onclick="App.submitVCERStage(${_V.projectId})">
        🚀 提交 — ${VCER_STAGE_NAMES[stage]}</button>
        </form>`;
    bodyEl.innerHTML = html;
    // 表单是动态注入的，CustomSelect 的启动扫描扫不到，得就地初始化
    CustomSelect.initAll(bodyEl);

    // 阶段一：把「十年预估减排总量」默认填成最新 PDD 的值
    if (stage === 1) _vcerTenYearInit(_V.projectId);
}

function _esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ── 项目编号预分配（先预订后转正，空号不递补）── */
async function reserveCode(projectId) {
    if (!confirm('将为该项目预分配一个 VCER 编号（如 GDES-VCER-企业序号-项目序号）：\n\n'
        + '· PDD 可直接使用该编号编制，无需等审核通过\n'
        + '· 内部审核通过后编号自动转正\n'
        + '· 项目废弃时编号作废留痕，不会递补给其他项目\n\n确认预分配？')) return;
    const data = await API.post(`/api/projects/${projectId}/reserve-code`, {});
    if (data.status === '成功') {
        let msg = '✅ ' + (data.msg || '预分配成功');
        if (data.warn) msg += '\n\n⚠ ' + data.warn;
        alert(msg);
        loadProjects();
        loadDashboard();
    } else {
        alert('预分配失败: ' + (data.msg || '未知错误'));
    }
}

/* ── VCER 编号自动回填 ── */
async function syncVcerCodes() {    if (!confirm('将从 VCER 平台拉取企业已注册项目，按名称匹配回填本地缺失的项目编号。\n已有编号的项目不会改动。继续？')) return;
    const data = await API.post('/api/projects/sync-vcer-codes', {});
    if (data.status !== '成功') {
        alert('回填失败: ' + (data.msg || '未知错误'));
        return;
    }
    let msg = `平台共 ${data.remote_count} 个已注册项目。\n`;
    if (data.matched && data.matched.length) {
        msg += `\n✅ 已回填 ${data.matched.length} 个：\n` +
            data.matched.map(m => `  ${m.name} → ${m.code}`).join('\n');
    } else {
        msg += '\n没有需要回填的项目（或均已匹配）。';
    }
    if (data.unmatched_local && data.unmatched_local.length) {
        msg += `\n\n⚠ 以下 ${data.unmatched_local.length} 个项目未能匹配，请人工核对：\n` +
            data.unmatched_local.map(m => '  ' + m.name).join('\n');
    }
    alert(msg);
    loadProjects();
    loadDashboard();
}

/* ── 提交阶段 ── */
async function submitVCERStage(projectId) {
    const form = document.getElementById('vcerForm');
    if (!form) return;
    const formData = new FormData(form);
    const stage = parseInt(formData.get('stage'));

    // === 业务规则校验 ===
    // 一次性阶段已通过则拒绝
    if (_isOneTimeStage(stage)) {
        const sd = _V.stages.find(s => s.stage === stage);
        if (sd && sd.status === '已通过') return alert(`阶段${stage}（${VCER_STAGE_NAMES[stage]}）已通过，不可重复提交。`);
    }

    // 轮次只用于展示。交易使用项目持有余额；抵消使用买方持有余额，
    // 后者只在促进会账本中可查，最终金额均由促进会再次校验。
    if ((stage === 4 || stage === 5) && _V.roundsLoaded && _V.roundCounts[3] <= 0) {
        return alert('请先完成至少一轮核查（阶段3）。');
    }
    if (stage === 4) {
        const amount = Number(formData.get('traded_amount'));
        if (!Number.isFinite(amount) || amount <= 0) return alert('请输入大于 0 的交易量。');
        // 本地余额可能尚未同步；促进会按实时项目余额作最终判断。
    }
    if (stage === 5) {
        const amount = Number(formData.get('offset_amount'));
        if (!String(formData.get('buyer_name') || '').trim()) return alert('请填写买方/抵消方名称。');
        if (!Number.isFinite(amount) || amount <= 0) return alert('请输入大于 0 的抵消量。');
    }

    // 基础字段校验
    if (stage === 1) {
        if (!formData.get('enterprise_name') || !formData.get('project_name'))
            return alert('请填写企业名称和项目名称');
        if (!formData.get('query_password') || formData.get('query_password').length < 4)
            return alert('查询密码至少4位');
    } else {
        if (!formData.get('query_password'))
            return alert('请输入查询密码');
    }

    if (stage===3) {
        const start=String(formData.get('verification_start')||'');
        const end=String(formData.get('verification_end')||'');
        if (!start||!end||start>end) return alert('请填写有效的核查起止日期。');
        try {
            const ack=await confirmContinuity(projectId,start,end);
            if (ack===null) return;
            formData.set('continuity_ack',ack);
        } catch(e) {return alert(e.message);}
    }
    if (!confirm(`确认提交「阶段${stage} — ${VCER_STAGE_NAMES[stage]}」？`)) return;

    try {
        const resp = await fetch(`/api/projects/${projectId}/vcer/submit`, { method: 'POST', body: formData });
        const data = await resp.json();

        if (data.status === '成功') {
            // 阶段1成功 → 显示密钥弹窗
            if (stage === 1 && data.query_key_saved) {
                showVCERKeyModal(data.query_key_saved);
            } else {
                alert(`提交成功！${data.msg || ''}`);
            }

            // 刷新状态
            await refreshVCERStatus(projectId);
            loadDashboard();
            loadProjects();
        } else {
            alert('提交失败: ' + (data.msg || '未知错误'));
        }
    } catch(e) {
        alert('网络错误: ' + e.message);
    }
}

/* ── 密钥弹窗 ── */
function showVCERKeyModal(queryKey) {
    document.getElementById('vcerKeyModalText').textContent = queryKey;
    document.getElementById('vcerCopyMsg').classList.add('hidden');
    const modal = document.getElementById('vcerKeyModal');
    modal.classList.remove('hidden', 'closing');

    document.getElementById('btnVcerCopyKey').onclick = () => {
        navigator.clipboard.writeText(queryKey).then(() => {
            document.getElementById('vcerCopyMsg').classList.remove('hidden');
        }).catch(() => {
            const ta = document.createElement('textarea');
            ta.value = queryKey;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            document.getElementById('vcerCopyMsg').classList.remove('hidden');
        });
    };
}

function closeVCERKeyModal() {
    const modal = document.getElementById('vcerKeyModal');
    if (modal.classList.contains('hidden') || modal.classList.contains('closing')) return;
    modal.classList.add('closing');
    modal.querySelector('.modal-card').addEventListener('animationend', function h() {
        this.removeEventListener('animationend', h);
        modal.classList.add('hidden');
        modal.classList.remove('closing');
    }, { once: true });
}

/* ── 轮次管理（阶段3/4/5 专用） ── */
// 直连 api.environdec.cn 拉取某阶段轮次（绕过本地后端中转）
function _vcerRoundsUrl(stage) {
    const base = (_V.adminBaseUrl || 'https://api.environdec.cn')
        .replace(/^http:\/\//i, 'https://');   // 直连走 https，避免 308 跳转
    return `${base}/cn/api/vcer/stage_rounds?key=${encodeURIComponent(_V.queryKey || '')}&stage=${stage}`;
}

async function _fetchRoundsDirect(stage) {
    const data = await fetch(_vcerRoundsUrl(stage)).then(r => r.json());
    // 兼容两种返回：正常 {success:true, rounds:[...]}；异常/无效 key 返回 [ {...}, code ]
    if (Array.isArray(data)) return [];
    if (data && data.success && Array.isArray(data.rounds)) return data.rounds;
    return [];
}

async function _loadRounds(stage) {
    const stackEl = document.getElementById('vcerRoundStack');
    const detailEl = document.getElementById('vcerRoundDetail');
    const newBtn = document.getElementById('btnVcerNewRound');
    const constraintEl = document.getElementById('vcerRoundConstraint');
    const historyLabel = document.getElementById('vcerHistoryStageLabel');

    historyLabel.textContent = `— 阶段${stage}`;

    if (!_isMultiRoundStage(stage)) {
        // 阶段1/2：简单显示阶段状态
        stackEl.innerHTML = '';
        newBtn.classList.add('hidden');
        newBtn.style.display = 'none';
        constraintEl.classList.add('hidden');
        constraintEl.style.display = 'none';
        const sd = _V.stages.find(s => s.stage === stage);
        if (sd) {
            const statusCls = sd.status === '已通过' ? 'passed' : sd.status === '已驳回' ? 'rejected' : 'pending';
            detailEl.innerHTML = `
                <div class="detail-header">
                    <span class="detail-round-label">${VCER_STAGE_NAMES[stage]}</span>
                    <span class="detail-status-tag ${statusCls}">${sd.status}</span>
                </div>
                <div class="detail-row"><span class="detail-key">状态</span><span class="detail-val">${sd.status}</span></div>
                ${sd.reason ? `<div class="detail-row"><span class="detail-key">驳回原因</span><span class="detail-val" style="color:var(--danger)">${_parseReasons(sd.reason)}</span></div>` : ''}
            `;
        } else {
            detailEl.innerHTML = '<p class="muted">暂无记录</p>';
        }
        return;
    }

    // 阶段3/4/5：加载轮次列表。先确保跨阶段轮数数据已就绪
    if (!_V.roundsLoaded) await _fetchRoundCounts();

    try {
        // 优先用已缓存轮次（进 Tab 时已并行预取），未命中再直连远端
        let rounds = _V.roundsCache[stage];
        if (rounds === undefined) {
            rounds = await _fetchRoundsDirect(stage);
            _V.roundsCache[stage] = rounds;
        }
        if (!rounds || !rounds.length) {
            stackEl.innerHTML = '';
            detailEl.innerHTML = '<p class="muted">暂无提交记录</p>';
            newBtn.classList.add('hidden');
            constraintEl.classList.add('hidden');
            return;
        }

        _V.rounds = rounds;

        // 更新已通过轮数
        const passedCount = rounds.filter(r => r.status === '已通过').length;
        _V.roundCounts[stage] = passedCount;

        // 渲染轮次堆叠卡片
        if (rounds.length === 0) {
            stackEl.innerHTML = '<span style="font-size:12px;color:#999">暂无轮次</span>';
        } else {
            stackEl.innerHTML = rounds.map((r, i) => {
                let cls = 'pending';
                if (r.status === '已通过') cls = 'passed';
                else if (r.status === '已驳回') cls = 'rejected';
                const selCls = _V.selectedRound === r.round ? ' selected' : '';
                return `<div class="vcer-round-chip ${cls}${selCls}" onclick="App.vcerSelectRound(${stage}, ${r.round})">
                    <span>🔵</span> 第${r.round}轮
                </div>`;
            }).join('');
        }

        // 判断是否可以新增一轮
        let canAdd = true;
        let constraintMsg = '';

        // 最新一轮在审核中或已驳回待处理 → 不能新增
        if (rounds.length > 0) {
            const latest = rounds[rounds.length - 1];
            if (latest.status === '待审核' || latest.status === '已驳回' || latest.status === '申请修改中') {
                canAdd = false;
            }
        }

        // 新轮次不受核查轮数限制；余额由促进会在提交时按持有人校验。
        if (stage === 4) {
            const project = state.projects.find(p => p.id === _V.projectId);
            const balance = Number(project?.available_assets_net);
            if (project?.available_assets_net != null && Number.isFinite(balance) && balance <= 0) {
                constraintMsg = '项目当前本地可用余额为 0。请先刷新项目状态；余额仍为 0 时无法交易。';
            }
        }

        if (canAdd) {
            newBtn.classList.remove('hidden');
            newBtn.style.display = '';
            newBtn.onclick = () => _startNewRound(stage);
            constraintEl.classList.toggle('hidden', !constraintMsg);
            constraintEl.style.display = constraintMsg ? '' : 'none';
            constraintEl.textContent = constraintMsg;
        } else {
            newBtn.classList.add('hidden');
            newBtn.style.display = 'none';
            if (constraintMsg) {
                constraintEl.classList.remove('hidden');
                constraintEl.style.display = '';
                constraintEl.textContent = constraintMsg;
            } else {
                constraintEl.classList.add('hidden');
                constraintEl.style.display = 'none';
            }
        }

        // 默认选中最后一轮
        if (rounds.length > 0 && !_V.selectedRound) {
            _V.selectedRound = rounds[rounds.length - 1].round;
            _renderRoundDetail(stage, _V.selectedRound, rounds);
        } else if (_V.selectedRound) {
            _renderRoundDetail(stage, _V.selectedRound, rounds);
        } else {
            detailEl.innerHTML = '<p class="muted">暂无轮次数据</p>';
        }

    } catch(e) {
        stackEl.innerHTML = '';
        detailEl.innerHTML = `<p class="muted">加载失败: ${e.message}</p>`;
        newBtn.classList.add('hidden');
        constraintEl.classList.add('hidden');
    }
}

function vcerSelectRound(stage, round) {
    _V.selectedRound = round;
    // 更新堆叠卡片高亮
    const chips = document.querySelectorAll('#vcerRoundStack .vcer-round-chip');
    chips.forEach(c => {
        c.classList.toggle('selected', c.textContent.trim().indexOf('第'+round+'轮') >= 0);
    });
    _renderRoundDetail(stage, round, _V.rounds);
}

function _renderRoundDetail(stage, round, rounds) {
    const detailEl = document.getElementById('vcerRoundDetail');
    const r = rounds.find(x => x.round === round);
    if (!r) {
        detailEl.innerHTML = '<p class="muted">未找到该轮次数据</p>';
        return;
    }

    const statusCls = r.status === '已通过' ? 'passed' : r.status === '已驳回' ? 'rejected' : 'pending';
    let html = `<div class="detail-header">
        <span class="detail-round-label">第${round}轮</span>
        <span class="detail-status-tag ${statusCls}">${r.status || ''}</span>
    </div>`;

    html += `<div class="detail-row"><span class="detail-key">状态</span><span class="detail-val">${r.status || '—'}</span></div>`;

    if (r.verified_reduction) {
        html += `<div class="detail-row"><span class="detail-key">核证减排量</span><span class="detail-val">${r.verified_reduction} tCO₂e</span></div>`;
    }
    if (r.transaction_reduction) {
        html += `<div class="detail-row"><span class="detail-key">交易量</span><span class="detail-val">${r.transaction_reduction} tCO₂e</span></div>`;
    }
    if (r.offset_reduction) {
        html += `<div class="detail-row"><span class="detail-key">抵消量</span><span class="detail-val">${r.offset_reduction} tCO₂e</span></div>`;
    }
    if (r.buyer_name) {
        html += `<div class="detail-row"><span class="detail-key">买方</span><span class="detail-val">${r.buyer_name}</span></div>`;
    }
    if (r.seller_name) {
        html += `<div class="detail-row"><span class="detail-key">卖方</span><span class="detail-val">${r.seller_name}</span></div>`;
    }
    if (r.date) {
        html += `<div class="detail-row"><span class="detail-key">时间</span><span class="detail-val">${r.date}</span></div>`;
    }
    if (r.reason) {
        html += `<div class="detail-row"><span class="detail-key">驳回原因</span><span class="detail-val" style="color:var(--danger)">${_parseReasons(r.reason)}</span></div>`;
    }

    // 文件下载链接（通过企业后端代理到 VCER 服务器）
    if (r.files && typeof r.files === 'object' && Object.keys(r.files).length > 0) {
        html += '<div class="detail-files">';
        Object.entries(r.files).forEach(([key, path]) => {
            const label = {'application_form':'申请书','design_doc':'设计文件','contract':'合同',
                'verification_report':'核算报告','certificate':'核证证书','offset_declaration':'抵消声明'}[key] || key;
            const dlUrl = `${_V.adminBaseUrl}/db/api/file_view?path=${encodeURIComponent(path)}&download=1`;
            html += `<a href="${dlUrl}" target="_blank" rel="noopener">📥 ${label}</a>`;
        });
        html += '</div>';
    }

    detailEl.innerHTML = html;
}

function _startNewRound(stage) {
    _V.selectedRound = null;
    // 清空选中状态
    document.querySelectorAll('#vcerRoundStack .vcer-round-chip').forEach(c => c.classList.remove('selected'));
    const passedCount = Math.max(0, _V.roundCounts[stage] || 0);
    document.getElementById('vcerRoundDetail').innerHTML =
        `<p class="muted">🆕 准备提交<strong>第${passedCount + 1}轮</strong> — 请在左侧填写并提交</p>`;
    // 渲染空白表单
    _renderFormHTML(stage);
    // 确保遮罩层关闭
    _showOverlay(null);
    // 更新标题
    document.getElementById('vcerFormTitle').innerHTML = `📝 阶段${stage}：${VCER_STAGE_NAMES[stage]} — 🆕 第${passedCount + 1}轮`;
    // 滚动到表单区
    document.getElementById('vcerFormBody').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ── 密码管理 ── */
function openVCERPwdModal() {
    document.getElementById('vcerPwdOld').value = '';
    document.getElementById('vcerPwdNew').value = '';
    document.getElementById('vcerPwdConfirm').value = '';
    document.getElementById('vcerPwdError').classList.add('hidden');
    const modal = document.getElementById('vcerPwdModal');
    modal.classList.remove('hidden', 'closing');

    document.getElementById('btnVcerPwdSubmit').onclick = async () => {
        const oldPwd = document.getElementById('vcerPwdOld').value.trim();
        const newPwd = document.getElementById('vcerPwdNew').value.trim();
        const confirmPwd = document.getElementById('vcerPwdConfirm').value.trim();
        const errEl = document.getElementById('vcerPwdError');

        if (!oldPwd) return errEl.classList.remove('hidden'), errEl.textContent = '请输入原密码';
        if (!newPwd || newPwd.length < 4) return errEl.classList.remove('hidden'), errEl.textContent = '新密码至少4位';
        if (newPwd !== confirmPwd) return errEl.classList.remove('hidden'), errEl.textContent = '两次输入的新密码不一致';

        try {
            const resp = await fetch(`/api/projects/${_V.projectId}/vcer/change_password`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({old_password: oldPwd, new_password: newPwd}),
            });
            const data = await resp.json();
            if (data.status === '成功') {
                _V.queryPassword = newPwd;
                try {
                    const p = state.projects.find(x => x.id == _V.projectId);
                    if (p) p.vcer_query_pwd = newPwd;
                } catch(e) {}
                alert('密码修改成功！');
                closeVCERPwdModal();
            } else {
                errEl.classList.remove('hidden');
                errEl.textContent = data.msg || '修改失败';
            }
        } catch(e) {
            errEl.classList.remove('hidden');
            errEl.textContent = '网络错误: ' + e.message;
        }
    };

    document.getElementById('btnVcerForgotPwd').onclick = async () => {
        if (!confirm('确定申请管理员重置密码？管理员批准后您可设置新密码。')) return;
        try {
            const resp = await fetch(`/api/projects/${_V.projectId}/vcer/request_password_reset`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
            });
            const data = await resp.json();
            if (data.status === '成功') {
                alert('已提交密码重置申请，请等待管理员审核。');
                closeVCERPwdModal();
            } else {
                alert('申请失败: ' + (data.msg || '未知错误'));
            }
        } catch(e) {
            alert('网络错误: ' + e.message);
        }
    };
}

function closeVCERPwdModal() {
    const modal = document.getElementById('vcerPwdModal');
    if (modal.classList.contains('hidden')) return;
    modal.classList.add('closing');
    modal.querySelector('.modal-card').addEventListener('animationend', function h() {
        this.removeEventListener('animationend', h);
        modal.classList.add('hidden');
        modal.classList.remove('closing');
    }, { once: true });
}

// ═══════════════════════════════════════════════════════════
// 设置
// ═══════════════════════════════════════════════════════════

/* ═══════════════════════════════════════════════════════════
   日志模块
   ═══════════════════════════════════════════════════════════ */

const LOG_PAGE_SIZE = 200;
// beforeId = 键集分页游标（取 id 小于它的）。不用 OFFSET —— 后端保留策略会从表头
// 删旧行，翻页时行会整体前移，OFFSET 会漏读或重复。
let logState = { level: 'all', projectId: '', beforeId: null, total: 0, shown: 0, loading: false };

const LOG_LEVEL_STYLE = {
    CRITICAL: { icon: '💥', cls: 'log-critical' },
    ERROR:    { icon: '❌', cls: 'log-error' },
    WARNING:  { icon: '⚠️', cls: 'log-warning' },
    INFO:     { icon: 'ℹ️', cls: 'log-info' },
    DEBUG:    { icon: '🔍', cls: 'log-debug' },
};

async function loadLogsTab(more) {
    const container = document.getElementById('logList');
    if (!container) return;
    const append = more === true;
    if (!append) { logState.beforeId = null; logState.shown = 0; }
    logState.loading = true;
    try {
        let url = `/api/logs?level=${encodeURIComponent(logState.level)}` +
                  `&limit=${LOG_PAGE_SIZE}`;
        if (logState.projectId) url += `&project_id=${encodeURIComponent(logState.projectId)}`;
        if (append && logState.beforeId != null) url += `&before_id=${logState.beforeId}`;
        const data = await API.get(url);
        if (!data || data.status !== '成功') {
            container.innerHTML = `<p class="muted" style="text-align:center;padding:20px">加载失败：${
                _esc((data && data.msg) || '未知错误')}</p>`;
            return;
        }
        logState.total = data.total || 0;
        const logs = data.logs || [];
        if (append) appendLogList(logs); else renderLogList(logs, false);
        logState.shown += logs.length;
        if (logs.length) logState.beforeId = logs[logs.length - 1].id;
        // 项目下拉只在第一页返回（避免翻页时重复拉取）
        if (data.projects && data.projects.length !== undefined) {
            renderLogProjectOptions(data.projects);
        }
        renderLogSummary();
    } catch (e) {
        container.innerHTML = `<p class="muted" style="text-align:center;padding:20px">加载失败：${_esc(e.message)}</p>`;
    } finally {
        logState.loading = false;
    }
}

// 填充「按项目筛选」下拉（只列出真的有日志的项目）
function renderLogProjectOptions(projects) {
    const sel = document.getElementById('logProjectSel');
    if (!sel) return;
    const cur = logState.projectId || '';
    sel.innerHTML = '<option value="">全部项目</option>' +
        projects.map(p => {
            const local = state.projects.find(x => String(x.id) === String(p.id));
            const code = p.project_code || local?.project_code || '';
            return `<option value="${p.id}" data-search-text="${_esc(p.name + ' ' + code)}">${_esc(p.name)}</option>`;
        }).join('');
    sel.value = cur;
    CustomSelect.refresh(sel);
}

function renderLogSummary() {
    const el = document.getElementById('logSummary');
    const more = document.getElementById('logMore');
    if (!el) return;
    const levelLabel = { all: '全部日志', warning: 'warning 和 error', error: '仅 error' }[logState.level] || '全部日志';
    let scope = levelLabel;
    if (logState.projectId) {
        const sel = document.getElementById('logProjectSel');
        const opt = sel && sel.options[sel.selectedIndex];
        scope = `${opt ? opt.text : '项目 #' + logState.projectId} · ${levelLabel}`;
    }
    const shown = logState.shown;
    el.textContent = logState.total
        ? `${scope}：共 ${logState.total} 条，已显示 ${shown} 条`
        : `${scope}：暂无日志`;
    if (more) more.style.display = (shown > 0 && shown < logState.total) ? '' : 'none';
}

function _logItemHtml(x) {
    const st = LOG_LEVEL_STYLE[x.level_name] || LOG_LEVEL_STYLE.INFO;
    // 日志里会带项目名等用户输入，必须转义后再插进 innerHTML
    const detail = (x.detail || '').trim();
    return `<div class="log-item ${st.cls}">
        <div class="log-icon" title="${_esc(x.level_name)}">${st.icon}</div>
        <div class="log-body">
            <div class="log-msg">${_esc(x.message || '')}</div>
            <div class="log-meta">${_esc(x.ts || '')} · ${_esc(x.level_name || '')}${
                x.logger ? ' · ' + _esc(x.logger) : ''}</div>
            ${detail ? `<pre class="log-detail hidden">${_esc(detail)}</pre>` : ''}
        </div>
        ${detail ? '<button class="btn btn-sm btn-outline log-toggle">详情</button>' : ''}
    </div>`;
}

function renderLogList(logs, append) {
    const container = document.getElementById('logList');
    if (!container) return;
    if (!logs.length) {
        container.innerHTML = '<p class="muted" style="text-align:center;padding:20px">暂无日志</p>';
        return;
    }
    container.innerHTML = logs.map(_logItemHtml).join('');
}

function appendLogList(logs) {
    const container = document.getElementById('logList');
    if (!container || !logs.length) return;
    container.insertAdjacentHTML('beforeend', logs.map(_logItemHtml).join(''));
}

// 展开/收起某条日志的 traceback（事件委托，列表是动态渲染的）
function initLogsTab() {
    const list = document.getElementById('logList');
    if (list && !list._bound) {
        list._bound = true;
        list.addEventListener('click', e => {
            const btn = e.target.closest('.log-toggle');
            if (!btn) return;
            const item = btn.closest('.log-item');
            const pre = item && item.querySelector('.log-detail');
            if (!pre) return;
            const open = !pre.classList.toggle('hidden');
            btn.textContent = open ? '收起' : '详情';
        });
    }
    const sel = document.getElementById('logLevelSel');
    if (sel && !sel._bound) {
        sel._bound = true;
        sel.addEventListener('change', () => {
            logState.level = sel.value || 'all';
            loadLogsTab();
        });
    }
    const btnRefresh = document.getElementById('btnLogRefresh');
    if (btnRefresh && !btnRefresh._bound) {
        btnRefresh._bound = true;
        btnRefresh.addEventListener('click', () => loadLogsTab());
    }
    const btnMore = document.getElementById('btnLogMore');
    if (btnMore && !btnMore._bound) {
        btnMore._bound = true;
        btnMore.addEventListener('click', () => loadLogsTab(true));
    }
    const projSel = document.getElementById('logProjectSel');
    if (projSel && !projSel._bound) {
        projSel._bound = true;
        projSel.addEventListener('change', () => {
            logState.projectId = projSel.value || '';
            loadLogsTab();
        });
    }
}

async function loadSettings() {
    const data = await API.get('/api/config');
    if (data) {
        document.getElementById('cfgName').value = data.enterprise_name || '';
        document.getElementById('cfgId').value = data.enterprise_id || '';
        document.getElementById('cfgApiUrl').value = data.vcer_api_url || '';
        // 密钥不回显本体（后端只给打码串）。输入框永远留空，
        // 靠下面的提示行告诉用户"已配置 / 没配"。
        const apiKeyEl = document.getElementById('cfgApiKey');
        apiKeyEl.value = '';
        apiKeyEl.placeholder = data.api_access_key_set ? '留空则不修改' : '尚未配置，粘贴促进会给的密钥';
        const hintEl = document.getElementById('cfgApiKeyHint');
        if (data.api_access_key_set) {
            hintEl.textContent = `✅ 已配置（${data.api_access_key_masked}）`
                + (data.api_access_key_dirty ? ' —— ⚠ 存的内容带了多余文字，已自动取用其中的密钥，建议重新粘贴一次' : '');
        } else {
            hintEl.textContent = '⚠ 尚未配置 —— 碳资产/项目同步会返回 401';
        }
        hintEl.style.color = data.api_access_key_set
            ? (data.api_access_key_dirty ? '#e67e22' : '#27ae60')
            : '#e74c3c';
        // 历史数据 API 平台地址/账号/密码（密码只回显打码状态）
        document.getElementById('cfgHistoryApiBase').value = data.history_api_base || '';
        document.getElementById('cfgHistoryApiUser').value = data.history_api_username || '';
        const hpwdEl = document.getElementById('cfgHistoryApiPwd');
        hpwdEl.value = '';
        const hpwdHint = document.getElementById('cfgHistoryApiPwdHint');
        if (data.history_api_password_set) {
            hpwdHint.textContent = `✅ 已配置（${data.history_api_password_masked}）`;
            hpwdHint.style.color = '#27ae60';
        } else {
            hpwdHint.textContent = '⚠ 尚未配置 —— 历史 API 拉取/回溯会因无凭据失败';
            hpwdHint.style.color = '#e67e22';
        }
        // ?? 而不是 ||：存的是 0（全绿电因子）时要回显 0，不能显示成空
        document.getElementById('cfgEfGrid').value = data.ef_grid_override ?? '';
        document.getElementById('cfgAnnualCodeEnabled').checked = !!data.annual_code_enabled;
        document.getElementById('cfgDevMode').checked = !!data.dev_mode;
        // 来自企业外载包的配置标记只读
        const packCfg = data._pack_config || [];
        const fieldMap = { ef_grid_override: 'cfgEfGrid', report_logo_path: null };
        for (const f of packCfg) {
            const elId = fieldMap[f];
            if (!elId) continue;
            const el = document.getElementById(elId);
            if (el) {
                el.disabled = true;
                el.title = '该配置来自企业预设外载包，只读（请修改 presets_packs 下的包文件）';
            }
        }
    }
    // 已加载的计算公式
    const formulasData = await API.get('/api/formulas');
    state.formulas = formulasData.formulas || [];
    const hookContainer = document.getElementById('hookList');
    if (!state.formulas.length) {
        hookContainer.innerHTML = '<p class="muted">未加载任何公式</p>';
    } else {
        let html = '';
        state.formulas.forEach(h => {
            html += `<div style="padding:10px 0;border-bottom:1px solid #f0f0f0">
                <strong>${h.label}</strong> <code>${h.name}</code> v${h.version}
                <br><small style="color:#999">${h.description}</small>
                <br><small>需要参数: ${(h.required_params||[]).join(', ') || '无'}</small>
            </div>`;
        });
        hookContainer.innerHTML = html;
    }
}

// ═══════════════════════════════════════════════════════════
// 事件处理
// ═══════════════════════════════════════════════════════════

async function saveConfig() {
    const data = {
        enterprise_name: document.getElementById('cfgName').value,
        enterprise_id: document.getElementById('cfgId').value,
        vcer_api_url: document.getElementById('cfgApiUrl').value,
        history_api_base: document.getElementById('cfgHistoryApiBase').value.trim(),
        history_api_username: document.getElementById('cfgHistoryApiUser').value.trim(),
        annual_code_enabled: document.getElementById('cfgAnnualCodeEnabled').checked ? 1 : 0,
        dev_mode: document.getElementById('cfgDevMode').checked ? 1 : 0,
    };
    // 历史 API 密码：留空 = 不修改（与促进会密钥同约定），填了才提交
    const hpwd = document.getElementById('cfgHistoryApiPwd').value;
    if (hpwd) data.history_api_password = hpwd;
    // 来自外载包的只读字段不提交（保留包配置，删包即恢复通用）
    const efGridEl = document.getElementById('cfgEfGrid');
    if (efGridEl && !efGridEl.disabled) {
        // 留空 = 用默认因子（存 NULL）；填 0 是合法值（全绿电 / 做 what-if），
        // 不能用 `parseFloat(...) || null` —— 那会把 0 抹成 NULL，等于没填。
        const _efRaw = efGridEl.value.trim();
        const _efNum = parseFloat(_efRaw);
        data.ef_grid_override = (_efRaw === '' || isNaN(_efNum)) ? null : _efNum;
    }
    // ⚠️ 这个字段必须显式带上 —— 漏了就是"填了密钥点保存没反应"。
    //    留空由后端解释为"不修改"（见 app.py::api_update_config），
    //    所以这里无条件提交是安全的。
    const keyEl = document.getElementById('cfgApiKey');
    const typedKey = keyEl ? keyEl.value.trim() : '';
    if (typedKey) data.api_access_key = typedKey;

    const resp = await API.post('/api/config', data);
    const ok = resp.status === '成功';
    UI.showStatus('cfgStatus', ok ? 'success' : 'error',
        ok ? (typedKey ? '配置保存成功，API 密钥已更新' : '配置保存成功')
           : (resp.msg || '保存失败'));
    loadEnterpriseInfo();
    // 保存成功后重载：密钥框本来就会清空（后端不回显密钥本体），
    // 提示行会变成"✅ 已配置（••••xxxx）"—— 那不是失败，是没回显。
    if (ok) loadSettings();
}

/** 清空已存的 API 密钥（后端约定 __clear__ 才是真清空，留空=不修改） */
async function clearApiKey() {
    if (!confirm('确定清空已保存的促进会 API 密钥？\n清空后碳资产/项目同步会立刻开始报 401。')) return;
    const resp = await API.post('/api/config', { api_access_key: '__clear__' });
    UI.showStatus('cfgStatus', resp.status === '成功' ? 'success' : 'error',
        resp.status === '成功' ? '密钥已清空' : (resp.msg || '清空失败'));
    loadSettings();
}

async function saveProject() {
    const id = document.getElementById('projEditId').value;

    const tenYearVal = document.getElementById('projTenYearTotal').value.trim();
    const annualVal = document.getElementById('projAnnualReduction').value.trim();

    // 数据源与公式参数已经下移到机器上，项目级只留基本信息和统计数据
    const data = {
        project_name: document.getElementById('projName').value,
        hook_name: document.getElementById('projHook').value,
        project_summary: document.getElementById('projSummary').value,
        baseline_date: document.getElementById('projBaseline').value,
        pending_validation_10yr: tenYearVal === '' ? null : parseFloat(tenYearVal),
        estimated_annual_reduction: annualVal === '' ? null : parseFloat(annualVal),
        vcer_query_key: document.getElementById('projVcerKey').value,
        vcer_query_pwd: document.getElementById('projVcerPwd').value,
    };

    if (!data.project_name || !data.hook_name) return alert('项目名称和计算公式为必填项');

    let resp;
    if (id) {
        resp = await API.put(`/api/projects/${id}`, data);
    } else {
        resp = await API.post('/api/projects', data);
    }

    if (resp.status === '成功') {
        UI.closeProjectModal();
        await loadProjects();
        loadDashboard();
        // 新项目此刻才有 id，补建默认「1号机器」并把弹窗重新打开，让用户马上能配机器
        if (!id && resp.id) {
            try { await API.post(`/api/projects/${resp.id}/machines`, {}); } catch(e) {}
            const p = state.projects.find(x => x.id === resp.id);
            if (p) UI.openProjectModal(p);
        }
    } else {
        alert('保存失败: ' + (resp.msg || '未知错误'));
    }
}

async function saveManualData() {
    const pid = document.getElementById('manualProjId').value;
    const ts = document.getElementById('manualTs').value;
    const flow = parseFloat(document.getElementById('manualFlow').value) || 0;
    const power = parseFloat(document.getElementById('manualPower').value) || 0;

    if (!ts) return alert('请选择时间');
    if (flow <= 0 && power <= 0) return alert('请填写有效数据');

    const body = {
        raw_data: { cumulative_flow: flow, cumulative_power: power },
        timestamp: ts.replace('T', ' ') + ':00',
    };
    // 下拉为空时不传，后端默认落到项目第一台机器
    const mid = document.getElementById('manualMachineSel').value;
    if (mid) body.machine_id = parseInt(mid);

    const resp = await API.post(`/api/projects/${pid}/monitoring/manual`, body);

    if (resp.status === '成功') {
        UI.closeManualModal();
        alert(`计算完成!\n本期减排量: ${resp.result.carbon_reduction} tCO₂e\n累计减排量: ${resp.result.cumulative_reduction} tCO₂e`);
        loadDashboard();
    } else {
        alert('录入失败: ' + (resp.msg || '未知错误'));
    }
}

async function syncProject(projectId) {
    const resp = await API.post(`/api/projects/${projectId}/sync`);
    if (resp.status === '成功') {
        const details = resp.details ? '\n' + resp.details.join('\n') : '';
        alert('同步完成' + details);
        loadProjects();
        loadDashboard();
    } else {
        alert('同步失败: ' + (resp.msg || ''));
    }
}

/**
 * 刷新所有项目的状态。
 *
 * 逐个项目用**它自己的查询密钥+密码**向促进会查询，把项目编号（缺失时补全）、
 * 碳资产、阶段状态写回本地。
 *
 * 以前的实现是前端循环调单项目接口，有三个问题：
 *   ① `if (p.project_code)` 把没有编号的项目直接跳过了 —— 而这些项目恰恰最需要
 *      用查询密钥去把编号拉回来
 *   ② 每个项目弹一次 alert，10 个项目就是 10 个弹窗
 *   ③ 每个项目都重新拉一遍项目列表和仪表盘
 * 现在改成后端一次批量 + 前端渲染逐项目结果。
 */
async function syncAllProjects() {
    if (!state.projects.length) { alert('还没有项目'); return; }
    if (!confirm(`将从促进会刷新所有项目的编号、碳资产与阶段状态。

· 依据每个项目自己的查询密钥去查
· 没有查询密钥的项目会跳过并说明原因

继续？`)) return;

    const btn = document.getElementById('btnSyncAll');
    const card = document.getElementById('syncAllCard');
    const box = document.getElementById('syncAllResults');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ 正在刷新...';
    card.classList.remove('hidden');
    box.innerHTML = '<p class="muted">正在逐个向促进会查询，请稍候...</p>';

    try {
        const data = await API.post('/api/projects/sync-all', {});
        if (data.status !== '成功') {
            box.innerHTML = `<p class="status-msg error">刷新失败：${_esc(data.msg || '未知错误')}</p>`;
            return;
        }
        const results = data.results || [];
        const partial = data.partial_count || 0;
        const bad = data.total - data.ok_count - partial;
        const head = `<p class="muted" style="margin-bottom:8px">共 ${data.total} 个项目：`
            + `完全成功 <strong>${data.ok_count}</strong> 个`
            + (partial ? `，部分成功 <strong style="color:var(--warning)">${partial}</strong> 个` : '')
            + (bad ? `，失败/跳过 <strong style="color:var(--danger)">${bad}</strong> 个` : '')
            + '</p>';
        box.innerHTML = head + results.map(r => {
            // 三种状态：全好 ✅ / 有项没成 ⚠️ / 整体失败 ❌
            const icon = r.ok ? '✅' : (r.partial ? '⚠️' : '❌');
            const cls = r.ok ? 'ok' : (r.partial ? 'partial' : 'fail');
            const tail = r.ok ? '' : ' — ' + _esc(r.msg || '');
            const detail = (r.details || []).length
                ? `<div class="muted" style="font-size:12px;margin-top:2px">${
                    r.details.map(d => _esc(d)).join('<br>')}</div>`
                : '';
            return `<div class="sync-result-item ${cls}">
                <div>${icon} <strong>${_esc(r.name || ('项目 #' + r.id))}</strong>${tail}</div>${detail}</div>`;
        }).join('');
        loadProjects();
        loadDashboard();
    } catch (e) {
        box.innerHTML = `<p class="status-msg error">网络错误：${_esc(e.message)}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = orig;
    }
}

async function collectNow(projectId, button) {
    const btn = button || window.event?.currentTarget || window.event?.target;
    if (!btn) return;
    const origText = btn.textContent;
    btn.textContent = '⏳ 采集中...';
    btn.disabled = true;
    try {
        const resp = await API.post(`/api/projects/${projectId}/collect`, {});
        if (resp.status === '成功') {
            // 多机器时后端只给 results 列表；只有一台时额外带老字段 result/msg
            const rs = resp.results || [];
            if (rs.length > 1) {
                const lines = rs.map(r => {
                    if (r.status === '成功') {
                        const rr = r.result || {};
                        return rr.stored != null
                            ? `· ${r.machine_name}: ${r.msg || ('新增入账 ' + rr.stored + ' 条')}`
                            : `· ${r.machine_name}: 本期 ${rr.carbon_reduction ?? '—'} tCO₂e`;
                    }
                    return `· ${r.machine_name}: 失败 — ${r.msg || '未知错误'}`;
                });
                const okCount = rs.filter(r => r.status === '成功').length;
                alert(`✅ 采集完成：成功 ${okCount}/${rs.length} 台\n` + lines.join('\n'));
            } else {
                const r = resp.result || {};
                alert(r.stored != null
                    ? `✅ 采集完成：${resp.msg || ('新增入账 ' + r.stored + ' 条')}。`
                    : `✅ 采集成功！\n该机器本期减排: ${r.carbon_reduction ?? '—'} tCO₂e\n该机器累计减排: ${r.cumulative_reduction ?? '—'} tCO₂e`);
            }
            loadDashboard();
            loadProjects();
        } else {
            alert('采集失败: ' + (resp.msg || '未知错误'));
        }
    } catch(e) {
        alert('网络错误: ' + e.message);
    } finally {
        btn.textContent = origText;
        btn.disabled = false;
    }
}

async function deleteProject(projectId) {
    if (!confirm('确认删除此项目？所有关联数据将被删除。')) return;
    const resp = await API.del(`/api/projects/${projectId}`);
    if (resp.status === '成功') {
        loadProjects();
        loadDashboard();
    } else {
        alert('删除失败: ' + (resp.msg || ''));
    }
}

// ── 辅助 ──

function parseJsonField(id) {
    const val = document.getElementById(id).value.trim();
    if (!val) return {};
    try { return JSON.parse(val); }
    catch(e) {
        // JSON 格式错误时弹窗提示，防止存入烂数据
        alert('JSON 格式错误，请检查:\n' + e.message + '\n\n字段: ' + id);
        throw e;
    }
}

async function loadEnterpriseInfo() {
    const data = await API.get('/api/config');
    document.getElementById('entName').textContent = data.enterprise_name || '未配置企业';
    document.getElementById('entId').textContent = data.enterprise_id ? `企业序号: ${data.enterprise_id}` : '';
}

// ═══════════════════════════════════════════════════════════
// 初始化
// ═══════════════════════════════════════════════════════════

async function init() {
    UI.initTabs();
    loadEnterpriseInfo();
    loadSettings();    // 加载公式列表到 state.formulas
    loadDashboard();

    // 项目弹窗事件
    document.getElementById('btnNewProject').addEventListener('click', () => UI.openProjectModal(null));
    document.getElementById('projectSearchInput').addEventListener('input', renderProjectList);
    document.getElementById('btnModalClose').addEventListener('click', UI.closeProjectModal);
    document.getElementById('btnModalCancel').addEventListener('click', UI.closeProjectModal);
    document.getElementById('btnModalSave').addEventListener('click', saveProject);
    document.getElementById('btnAddMachine').addEventListener('click', machineAdd);
    document.getElementById('projHook').addEventListener('change', () => {
        // 公式换了，机器编辑表单里的参数提示跟着重取
        if (machineState.editingId !== null) machineHookHint();
    });

    // 设置
    document.getElementById('btnSaveConfig').addEventListener('click', saveConfig);
    document.getElementById('btnClearApiKey').addEventListener('click', clearApiKey);

    // 同步
    document.getElementById('btnSyncAll').addEventListener('click', syncAllProjects);
    const btnSyncVcerCodes = document.getElementById('btnSyncVcerCodes');
    if (btnSyncVcerCodes) btnSyncVcerCodes.addEventListener('click', syncVcerCodes);

    // 手动数据
    document.getElementById('btnManualSave').addEventListener('click', saveManualData);

    // 断档兜底：确认框里的「确认执行」
    document.getElementById('btnGapApply').addEventListener('click', confirmGapApply);

    // 历史数据批量回溯
    document.getElementById('btnBackfillAll').addEventListener('click', () => openBackfillModal(null));
    document.getElementById('btnBackfillStart').addEventListener('click', startBackfill);

    // 报告
    document.getElementById('btnGenerateReport').addEventListener('click', () => generateReport());
    document.getElementById('reportTypeSel').addEventListener('change', () => {
        toggleReportDateRange();
        updateReportDateLabels();   // 两个日期框的含义随报告类型变
        loadTemplates();
        updateReportGate();
        loadTenYearTotal();
    });
    document.getElementById('reportProjectSel').addEventListener('change', () => onReportProjectChange());
    // 选模板 → 识别模板字段并在预设管理中高亮
    document.getElementById('reportTemplateSel').addEventListener('change', function() {
        loadTemplateKeys(this.value);
    });
    // 占位符搜索
    const placeholderSearch = document.getElementById('placeholderSearch');
    if (placeholderSearch) placeholderSearch.addEventListener('input', filterPlaceholders);
    // 可用字段子标签切换
    document.querySelectorAll('.var-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchVarTab(btn.dataset.varTab));
    });
    // 预设管理
    const btnAddPreset = document.getElementById('btnAddPreset');
    if (btnAddPreset) btnAddPreset.addEventListener('click', () => openPresetModal(null));
    const btnPresetSave = document.getElementById('btnPresetSave');
    if (btnPresetSave) btnPresetSave.addEventListener('click', savePreset);

    // ── 全项目通用预设按钮 ──
    const btnAddGlobalPreset = document.getElementById('btnAddGlobalPreset');
    if (btnAddGlobalPreset) btnAddGlobalPreset.addEventListener('click', () => openGlobalPresetModal(null));
    // ── 预设批量编辑 ──
    const btnPresetBatchExport = document.getElementById('btnPresetBatchExport');
    if (btnPresetBatchExport) btnPresetBatchExport.addEventListener('click', exportPresetBatch);
    const btnPresetBatchImport = document.getElementById('btnPresetBatchImport');
    if (btnPresetBatchImport) btnPresetBatchImport.addEventListener('click', importPresetBatch);
    const presetBatchFile = document.getElementById('presetBatchFile');
    if (presetBatchFile) presetBatchFile.addEventListener('change', function() {
        document.getElementById('presetBatchFileName').textContent = this.files[0] ? this.files[0].name : '未选择文件';
    });

    // ── 项目预设按钮 ──
    const btnAddProjectPreset = document.getElementById('btnAddProjectPreset');
    if (btnAddProjectPreset) btnAddProjectPreset.addEventListener('click', () => {
        document.getElementById('presetModal').dataset.mode = 'project';
        openProjectPresetModal(null);
    });

    // 预设字段拖拽：企业预设 ↔ 全项目通用 ↔ 项目预设
    ['enterprise', 'global', 'project'].forEach(zone => {
        const zoneEl = document.getElementById(getZoneElId(zone));
        if (!zoneEl) return;
        zoneEl.addEventListener('dragover', (e) => presetZoneDragOver(e, zone));
        zoneEl.addEventListener('dragleave', (e) => presetZoneDragLeave(e, zone));
        zoneEl.addEventListener('drop', (e) => presetZoneDrop(e, zone));
    });

    // 监测刷新
    document.getElementById('btnRefreshMonitor').addEventListener('click', refreshMonitor);
    // 监测手动采集
    document.getElementById('btnCollectMonitor').addEventListener('click', async () => {
        const sel = document.getElementById('monitorProjectSel');
        const pid = parseInt(sel.value);
        if (!pid) return alert('请先选择一个项目');
        await collectNow(pid, document.getElementById('btnCollectMonitor'));
        refreshMonitor();
    });

    // VCER
    document.getElementById('btnRefreshVCER').addEventListener('click', () => refreshVCERStatus());
    document.getElementById('btnVcerChangePwd').addEventListener('click', openVCERPwdModal);

    // 数据导入
    const importFileInput = document.getElementById('importFileInput');
    if (importFileInput) {
        importFileInput.addEventListener('change', function() {
            const file = this.files[0];
            if (!file) return;
            document.getElementById('importFileName').textContent = file.name;
        });
        document.getElementById('btnStartImport').addEventListener('click', startImport);
        const btnTplCsv = document.getElementById('btnImportTemplateCsv');
        if (btnTplCsv) btnTplCsv.addEventListener('click', () => downloadImportTemplate('csv'));
        const btnTplXlsx = document.getElementById('btnImportTemplateXlsx');
        if (btnTplXlsx) btnTplXlsx.addEventListener('click', () => downloadImportTemplate('xlsx'));
    }

    // 代码控制台
    const btnRunCode = document.getElementById('btnRunCode');
    if (btnRunCode) btnRunCode.addEventListener('click', runConsoleCode);

    // VCER 密钥弹窗关闭（通过 HTML 中的 onclick="App.closeVCERKeyModal()" 处理）
}

// ═══════════════════════════════════════════════════════════
// 断档兜底：检测 → 人工确认 → 执行
//
// 自动兜底默认已关（config.COLLECT_GAP_AUTO_BRIDGE=0）。断档保护只把那一行记成 0 + 告警，
// 由人在这里看过「怎么算的 / 会改成什么」再确认。算法全在后端 core/gap_bridge.py，
// 前端只负责把预览结果画出来 —— **不在前端复算任何数字**。
// ═══════════════════════════════════════════════════════════

function _closeModalAnimated(modal) {
    // 与 closeManualModal 同一套退场写法：直接 add('hidden') 会跳过关闭动画
    if (!modal || modal.classList.contains('hidden')) return;
    modal.classList.add('closing');
    modal.querySelector('.modal-card').addEventListener('animationend', function h() {
        this.removeEventListener('animationend', h);
        if (!modal.classList.contains('closing')) return;
        modal.classList.add('hidden');
        modal.classList.remove('closing');
    }, { once: true });
}

function _gapNum(v, d = 6) {
    return (v === null || v === undefined || v === '') ? '—' : UI.fmtNum(v, d);
}

// ── 待兜底清单 ──
async function openGapList(projectId) {
    const p = state.projects.find(x => x.id === projectId);
    document.getElementById('gapListBody').innerHTML = '<p class="muted">加载中...</p>';
    const modal = document.getElementById('gapListModal');
    modal.classList.remove('hidden', 'closing');
    modal.dataset.projectId = projectId;

    const resp = await API.get('/api/gap_bridge/pending');
    const items = ((resp && resp.data) || []).filter(x => x.project_id === projectId);
    const box = document.getElementById('gapListBody');

    if (!items.length) {
        box.innerHTML = '<p class="muted">这个项目当前没有待兜底的断档。</p>';
        return;
    }
    // 待兜底的排前面，已兜底的排后面（已兜底的可以重算覆盖）
    items.sort((a, b) => (a.bridged ? 1 : 0) - (b.bridged ? 1 : 0));

    const rows = items.map(it => `
        <tr>
            <td>#${it.machine_id} ${_esc(it.machine_name || '')}</td>
            <td>${_esc(it.prev_timestamp || '（无起点）')}</td>
            <td>${_esc(it.gap_timestamp || '')}</td>
            <td>${it.span_hours === null ? '—' : UI.fmtNum(it.span_hours / 24, 1) + ' 天'}</td>
            <td>${it.bridged
                ? '<span class="muted">已兜底（可重算覆盖）</span>'
                : '<span style="color:#e74c3c;font-weight:600">待兜底</span>'}</td>
            <td style="white-space:nowrap">
                <button class="btn btn-sm ${it.bridged ? 'btn-outline' : 'btn-primary'}"
                        onclick="App.openGapConfirm(${it.gap_row_id})">兜底</button>
            </td>
        </tr>`).join('');

    box.innerHTML = `
        <div class="muted" style="margin-bottom:8px">
            项目「${_esc((p && p.project_name) || '')}」共 ${items.length} 处断档
            （待兜底 ${items.filter(x => !x.bridged).length} 处）
        </div>
        <table class="data-table">
            <thead><tr>
                <th>机器</th><th>空档起点</th><th>断档行</th><th>空档时长</th><th>状态</th><th></th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

function closeGapListModal() {
    _closeModalAnimated(document.getElementById('gapListModal'));
}

// ── 确认框（计算结果 + 替换结果 + 流程）──
async function openGapConfirm(gapRowId, verifiedManual) {
    const body = document.getElementById('gapConfirmBody');
    const btn = document.getElementById('btnGapApply');
    btn.disabled = true;
    body.innerHTML = '<p class="muted">正在计算...</p>';

    const modal = document.getElementById('gapConfirmModal');
    modal.classList.remove('hidden', 'closing');

    let url = `/api/gap_bridge/preview?gap_row_id=${gapRowId}`;
    if (verifiedManual !== undefined && verifiedManual !== null && verifiedManual !== '') {
        url += `&verified=${encodeURIComponent(verifiedManual)}`;
    }
    const resp = await API.get(url);
    if (!resp || resp.status !== '成功') {
        body.innerHTML = `<p style="color:#e74c3c">取预览失败：${_esc((resp && resp.msg) || '未知错误')}</p>`;
        return;
    }
    state.gapPreview = resp.data;
    renderGapConfirm();
}

function renderGapConfirm() {
    const d = state.gapPreview || {};
    const body = document.getElementById('gapConfirmBody');
    const btn = document.getElementById('btnGapApply');
    const bf = d.before || {}, af = d.after || {};
    const srcText = d.verified_source === 'manual'
        ? '<span style="color:#e67e22">手工指定</span>' : '自动从促进会拉取';

    // ① 全段
    let html = `
        <div class="muted" style="line-height:1.7;margin-top:0">
            机器 <b>#${d.machine_id} ${_esc(d.machine_name || '')}</b>
            ｜空档 <b>${_esc(d.prev_timestamp || '（无起点）')}</b> →
            <b>${_esc(d.gap_timestamp || '')}</b>
            （${UI.fmtNum((d.span_hours || 0) / 24, 1)} 天）
        </div>`;

    if (d.already_bridged) {
        html += `<div style="background:#fff8e1;border:1px solid #f0d8a8;border-radius:8px;
                    padding:8px 12px;margin:10px 0;font-size:13px">
                    ⚠ 这次断档<b>之前已经兜底过</b>，确认后会按当前已核查量重新计算并覆盖原值。
                 </div>`;
    }

    html += `<h4 style="margin:14px 0 6px">① 全段减排量（空档两头的原始读数，按公式算）</h4>
        <table class="breakdown-table">
            <tr><td>全段减排量 R_span</td><td>${_gapNum(d.r_span)} tCO₂e</td></tr>
            <tr><td>基准线排放</td><td>${_gapNum(af.baseline_emissions_tco2)}</td></tr>
            <tr><td>项目排放</td><td>${_gapNum(af.project_emissions_tco2)}</td></tr>
        </table>

        <h4 style="margin:14px 0 6px">② 已核查量（与空档重叠的「已通过」核查轮次，按重叠天数摊分）</h4>
        <div class="muted" style="font-size:12px;margin-bottom:4px">来源：${srcText}</div>`;

    const detail = d.rounds_detail || [];
    if (detail.length) {
        html += `<table class="data-table"><thead><tr>
                    <th>轮次</th><th>核查期间</th><th>核证量</th><th>落在空档内</th><th>摊分比例</th><th>摊得量</th>
                 </tr></thead><tbody>` + detail.map(x => `
                    <tr><td>第 ${x.round} 轮</td>
                        <td>${_esc(x.start)} ~ ${_esc(x.end)}</td>
                        <td>${_gapNum(x.amount, 4)}</td>
                        <td>${x.days} / ${x.total_days} 天</td>
                        <td>${(x.ratio * 100).toFixed(1)}%</td>
                        <td><b>${_gapNum(x.prorated, 4)}</b></td></tr>`).join('') +
                 `</tbody></table>`;
    } else if (d.verified_source === 'manual') {
        html += '<p class="muted">手工指定，未按轮次摊分。</p>';
    } else {
        html += `<p class="muted">没有任何核查轮次与这段空档重叠 → 不做扣减，整段都会补记。</p>`;
    }
    html += `<table class="breakdown-table">
            <tr><td>已核查量 V（合计）</td><td>${_gapNum(d.verified)} tCO₂e</td></tr></table>`;

    // ③ 补记量
    html += `<h4 style="margin:14px 0 6px">③ 补记量</h4>
        <table class="breakdown-table">
            <tr><td>R_span − V</td>
                <td>${_gapNum(d.r_span)} − ${_gapNum(d.verified)} = <b>${_gapNum(d.booked)}</b> tCO₂e</td></tr>
        </table>`;

    // ④ 替换
    html += `<h4 style="margin:14px 0 6px">④ 将把「断档行」改成</h4>
        <table class="data-table"><thead><tr>
            <th>字段</th><th>现在</th><th>改后</th></tr></thead><tbody>
            <tr><td>减排量 carbon_reduction</td>
                <td>${_gapNum(bf.carbon_reduction)}</td>
                <td><b>${_gapNum(af.carbon_reduction)}</b></td></tr>
            <tr><td>本机器累计 cumulative_reduction</td>
                <td>${_gapNum(bf.cumulative_reduction)}</td>
                <td><b>${_gapNum(af.cumulative_reduction)}</b></td></tr>
            <tr><td>breakdown 标记</td><td>${_esc(bf.note || '（断档说明）')}</td>
                <td>打上 bridge 标记（报告里能看出这是兜底补记，不是原始采集）</td></tr>
        </tbody></table>`;

    // ⑤ 流程
    html += `<h4 style="margin:14px 0 6px">⑤ 执行流程</h4>
        <ol class="muted" style="line-height:1.9;margin:0;padding-left:22px">`
        + (d.steps || []).map(s => `<li>${_esc(String(s).replace(/^[①②③④⑤⑥⑦⑧]\s*/, ''))}</li>`).join('')
        + `</ol>`;

    // 手工指定已核查量（拉不到核查记录、或已知平台数据不对时用）
    html += `<h4 style="margin:16px 0 6px">手工指定已核查量（可选）</h4>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <input type="number" step="0.000001" id="gapVerifiedInput" class="input"
                   style="max-width:220px" placeholder="留空 = 用自动拉到的"
                   value="${d.verified_source === 'manual' ? d.verified : ''}">
            <button class="btn btn-sm btn-outline" onclick="App.recalcGapPreview()">按这个值重新计算</button>
            <span class="muted" style="font-size:12px">拉不到核查记录时，只有填了才能执行</span>
        </div>`;

    // 报错/警告
    if (!d.ok) {
        html += `<div style="background:#fdedec;border:1px solid #f5b7b1;border-radius:8px;
                    padding:10px 12px;margin-top:14px;font-size:13px;color:#c0392b">
                    <b>这段现在不能兜底：</b>${_esc(d.error || '')}
                 </div>`;
    }
    (d.warnings || []).forEach(w => {
        html += `<div class="muted" style="margin-top:8px;font-size:13px">⚠ ${_esc(w)}</div>`;
    });

    body.innerHTML = html;
    btn.disabled = !d.ok;
    btn.textContent = d.ok ? `确认执行（补记 ${UI.fmtNum(d.booked, 4)}）` : '确认执行';
}

function recalcGapPreview() {
    const d = state.gapPreview || {};
    const v = document.getElementById('gapVerifiedInput');
    openGapConfirm(d.gap_row_id, v ? v.value : '');
}

function closeGapConfirmModal() {
    state.gapPreview = null;
    _closeModalAnimated(document.getElementById('gapConfirmModal'));
}

async function confirmGapApply() {
    const d = state.gapPreview;
    if (!d || !d.gap_row_id) return;
    const vEl = document.getElementById('gapVerifiedInput');
    const manual = vEl && vEl.value !== '' ? parseFloat(vEl.value) : null;
    const what = `确认执行这次断档兜底？\n\n` +
        `机器 #${d.machine_id} ${d.machine_name || ''}\n` +
        `空档 ${d.prev_timestamp || '（无起点）'} → ${d.gap_timestamp}\n` +
        `补记 ${UI.fmtNum(d.booked, 6)} tCO₂e` +
        (d.already_bridged ? '\n\n（这次断档之前兜底过，会重算并覆盖原值）' : '') +
        `\n\n执行前会自动备份数据库。`;
    if (!confirm(what)) return;

    const btn = document.getElementById('btnGapApply');
    btn.disabled = true;
    btn.textContent = '执行中...';
    const resp = await API.post('/api/gap_bridge/apply',
        manual === null ? { gap_row_id: d.gap_row_id }
                        : { gap_row_id: d.gap_row_id, verified: manual });
    if (!resp || resp.status !== '成功') {
        alert('执行失败：' + ((resp && resp.msg) || '未知错误'));
        btn.disabled = false;
        btn.textContent = '确认执行';
        return;
    }
    const w = (resp.data && resp.data.written) || {};
    closeGapConfirmModal();
    showToast(`已补记 ${UI.fmtNum(w.carbon_reduction, 4)} tCO₂e`);
    // 清单和项目卡片都要刷新（角标要减一）
    const pid = document.getElementById('gapListModal').dataset.projectId;
    await loadProjects();
    if (pid && !document.getElementById('gapListModal').classList.contains('hidden')) {
        await openGapList(parseInt(pid, 10));
    }
}

// ── 历史数据批量回溯 ──
// 全局按钮回溯所有项目；项目卡片上的「回溯历史」只回溯那一个项目。
// 任务在后端后台线程跑，前端轮询状态；幂等 —— 已存在的时间戳自动跳过。
const backfillState = { projectId: null, jobId: null, timer: null };

function openBackfillModal(projectId) {
    backfillState.projectId = projectId || null;
    const p = projectId ? state.projects.find(x => x.id === projectId) : null;
    document.getElementById('bfProject').value = p
        ? `${p.project_name}（#${projectId}）`
        : '全部项目（所有「历史 API 定时拉取」机器）';
    document.getElementById('bfBaseline').value = '';
    const g = document.getElementById('bfGranularity');
    g.value = 'day';
    CustomSelect.refresh(g);
    const st = document.getElementById('bfStatus');
    st.classList.add('hidden');
    st.textContent = '';
    document.getElementById('bfResults').innerHTML = '';
    document.getElementById('btnBackfillStart').disabled = false;
    document.getElementById('backfillModal').classList.remove('hidden');
    loadBackfillJobs();
}

// 加载该项目的回溯任务历史（含运行中）
async function loadBackfillJobs() {
    const el = document.getElementById('bfJobs');
    if (!el) return;
    const url = backfillState.projectId
        ? `/api/projects/${backfillState.projectId}/backfill/jobs` : '/api/backfill/jobs';
    try {
        const resp = await API.get(url);
        const jobs = (resp && resp.jobs) || [];
        if (!jobs.length) { el.innerHTML = '<p class="muted">暂无回溯任务</p>'; return; }
        el.innerHTML = jobs.map(j => {
            const running = j.status === 'running';
            const dot = running ? '🟡' : (j.status === 'interrupted' ? '⚪' : '🟢');
            const statusText = running ? '运行中' : (j.status === 'interrupted' ? '服务重启后中断，可重新回溯补缺' : '已完成');
            let prog = '';
            if (j.progress && j.progress.retrying) {
                prog = ` · ${_esc(j.progress.machine_name || '设备')} 的 ${_esc(j.progress.current || '')} 请求失败，${_esc(j.progress.retry_at || '')} 再试（第 ${j.progress.retry_attempt}/3 次）`;
            } else if (j.progress && j.progress.total_days) {
                prog = ` · 已到 ${j.progress.current}（${j.progress.done_days}/${j.progress.total_days} 天，导入 ${j.progress.imported} 条）`;
            }
            const cnt = j.status === 'done' ? ` · 导入 ${j.imported} / 跳过 ${j.skipped} / 失败 ${j.failed}` : '';
            const time = j.started_at ? ` · ${j.started_at}` : '';
            return `<div style="padding:5px 0;border-bottom:1px solid rgba(0,0,0,.05)">
                ${dot} <b>${statusText}</b>${time}${prog}${cnt}
                ${j.baseline_date ? `<div class="muted">基准 ${_esc(j.baseline_date)}${j.granularity ? ' · ' + _esc(j.granularity) : ''}</div>` : ''}
            </div>`;
        }).join('');
    } catch(e) {
        el.innerHTML = `<p class="muted">加载失败: ${_esc(e.message)}</p>`;
    }
}

function closeBackfillModal() {
    if (backfillState.timer) { clearInterval(backfillState.timer); backfillState.timer = null; }
    backfillState.jobId = null;
    document.getElementById('backfillModal').classList.add('hidden');
}

async function startBackfill() {
    const btn = document.getElementById('btnBackfillStart');
    btn.disabled = true;
    const st = document.getElementById('bfStatus');
    st.classList.remove('hidden');
    st.textContent = '正在启动…';
    document.getElementById('bfResults').innerHTML = '';
    const body = {
        baseline_date: document.getElementById('bfBaseline').value.trim(),
        granularity: document.getElementById('bfGranularity').value,
    };
    try {
        const url = backfillState.projectId
            ? `/api/projects/${backfillState.projectId}/backfill` : '/api/backfill';
        const resp = await API.post(url, body);
        if (resp.status !== '成功') {
            st.textContent = '启动失败: ' + ((resp && resp.msg) || '未知错误');
            btn.disabled = false;
            return;
        }
        backfillState.jobId = resp.job_id;
        st.textContent = '回溯进行中…';
        backfillState.timer = setInterval(pollBackfillStatus, 1500);
        pollBackfillStatus();
    } catch(e) {
        st.textContent = '启动失败: ' + e.message;
        btn.disabled = false;
    }
}

async function pollBackfillStatus() {
    if (!backfillState.jobId) return;
    let job;
    try {
        const resp = await API.get(`/api/backfill/status/${backfillState.jobId}`);
        if (resp.status !== '成功') return;
        job = resp.job;
    } catch(e) { return; }

    document.getElementById('bfResults').innerHTML = (job.results || []).map(r => {
        const color = r.failed > 0 ? '#A32D2D' : (r.imported > 0 ? '#3B6D11' : '#5F5E5A');
        return `<div style="padding:4px 0;border-bottom:1px solid rgba(0,0,0,.08)">
            <b>${_esc(r.project_name)} · ${_esc(r.machine_name || ('机器#' + r.machine_id))}</b><br>
            <span style="color:${color}">导入 ${r.imported} 条 · 跳过已存在 ${r.skipped} 条 · 失败 ${r.failed} 条</span><br>
            <span class="muted">${_esc(r.msg || '')}</span></div>`;
    }).join('') || '<p class="muted">等待结果…</p>';

    const st = document.getElementById('bfStatus');
    if (job.status === 'done') {
        if (backfillState.timer) { clearInterval(backfillState.timer); backfillState.timer = null; }
        const tot = (job.results || []).reduce((a, r) => [a[0] + r.imported, a[1] + r.skipped, a[2] + r.failed], [0, 0, 0]);
        st.textContent = `完成：共导入 ${tot[0]} 条，跳过 ${tot[1]} 条，失败 ${tot[2]} 条` +
            (tot[0] === 0 && tot[2] === 0 && tot[1] > 0 ? '（历史数据已存在，无需回溯）' : '');
        document.getElementById('btnBackfillStart').disabled = false;
    } else {
        const p = job.progress;
        if (p && p.retrying) {
            st.textContent = `${p.machine_name || '设备'} 的 ${p.current || ''} 时间块请求失败；等待至 ${p.retry_at} 再试（第 ${p.retry_attempt}/3 次）。已有数据保留，后续时间块和机器会继续。`;
        } else if (p && p.total_days) {
            st.textContent = `回溯进行中… ${p.machine_name ? p.machine_name + ' · ' : ''}已到 ${p.current}（${p.done_days}/${p.total_days} 天，已导入 ${p.imported} 条）`;
        } else {
            st.textContent = `回溯进行中…（${(job.results || []).length}/${job.total} 台机器）`;
        }
        // 同步刷新下面的历史任务列表（运行中任务的进度实时更新）
        loadBackfillJobs();
    }
}

// ═══════════════════════════════════════════════════════════
// 数据查看（折线图 + 分页表格 + 删除）
// ═══════════════════════════════════════════════════════════
const dataState = { projectId: null, machineId: null, segmentId: null, anomaliesOnly: false, page: 1,
    pageSize: 50, total: 0, visibleRows: [], selected: new Set(), bulkAvailable: false };
let monitorReviewId = null;

function _parseRaw(raw) {
    try { return JSON.parse(raw || '{}'); } catch(e) { return {}; }
}

async function openDataModal(projectId, anomaliesOnly = false) {
    dataState.projectId = projectId;
    dataState.machineId = null;
    dataState.segmentId = null;
    dataState.anomaliesOnly = !!anomaliesOnly;
    dataState.selected.clear();
    dataState.bulkAvailable = false;
    try {
        const capability = await fetch(`/api/projects/${projectId}/monitoring/deleted-batches`);
        dataState.bulkAvailable = capability.ok;
    } catch (e) {}
    document.getElementById('dataAnomaliesOnly').checked = dataState.anomaliesOnly;
    dataState.page = 1;
    const p = state.projects.find(x => x.id === projectId);
    document.getElementById('dataProjectName').textContent = p ? p.project_name : ('#' + projectId);
    // 填机器下拉
    const sel = document.getElementById('dataMachineSel');
    sel.innerHTML = '<option value="">-- 全部机器 --</option>';
    try {
        const resp = await API.get(`/api/projects/${projectId}/machines`);
        (resp.machines || []).forEach(m => {
            sel.innerHTML += `<option value="${m.id}">${_esc(m.name)}</option>`;
        });
    } catch(e) {}
    CustomSelect.rebuild(sel);
    document.getElementById('dataModal').classList.remove('hidden');
    await loadDataList();
}

function closeDataModal() {
    document.getElementById('dataModal').classList.add('hidden');
}

function onDataMachineChange() {
    const sel = document.getElementById('dataMachineSel');
    dataState.machineId = sel.value ? parseInt(sel.value) : null;
    dataState.segmentId = null;
    dataState.page = 1;
    loadDataList();
}

function onDataAnomaliesChange() {
    dataState.anomaliesOnly = document.getElementById('dataAnomaliesOnly').checked;
    dataState.page = 1;
    loadDataList();
}

async function loadDataList() {
    const pid = dataState.projectId;
    if (!pid) return;
    const version=(dataState.loadVersion||0)+1;dataState.loadVersion=version;
    const machineId=dataState.machineId,segmentId=dataState.segmentId;
    const params = new URLSearchParams({ page: dataState.page, page_size: dataState.pageSize });
    if (dataState.machineId) params.set('machine_id', dataState.machineId);
    if (dataState.anomaliesOnly) params.set('anomalies_only', '1');
    if (dataState.segmentId) params.set('segment_id',dataState.segmentId);
    const [resp,segmentResp] = await Promise.all([
        API.get(`/api/projects/${pid}/monitoring/all?${params.toString()}`),
        API.get(`/api/projects/${pid}/monitoring/segments`+(dataState.machineId?`?machine_id=${dataState.machineId}`:''))]);
    if (version!==dataState.loadVersion) return;
    renderDataSegments(segmentResp);
    const data = (resp && resp.data) || [];
    const total = (resp && resp.total) || 0;
    dataState.total = total;
    dataState.visibleRows = data;
    dataState.selected.clear();
    document.getElementById('dataTotal').textContent = `共 ${total} 条${dataState.anomaliesOnly ? '受影响读数（可能属于同一次事件）' : ''}`;
    renderDataTable(data);
    updateDataSelection();
    renderDataPager(total);
    // 折线图：单独拉最近 500 条
    const chartResp = await API.get(`/api/projects/${pid}/monitoring/all?page=1&page_size=500`
        + (machineId ? `&machine_id=${machineId}` : '')
        + (segmentId ? `&segment_id=${segmentId}` : ''));
    if (version!==dataState.loadVersion) return;
    drawDataChart((chartResp && chartResp.data) || []);
}

function renderDataSegments(response) {
    const el=document.getElementById('dataSegments');
    if (!el) return;
    if (response.status!=='成功') {el.innerHTML='<p class="muted">'+_esc(response.msg||'阶段信息加载失败')+'</p>';return;}
    const fmt=v=>v==null?'—':UI.fmtNum(v,2);
    el.innerHTML='<div class="segment-toolbar"><b>按机器 / 计量阶段查看</b><button class="btn btn-sm btn-outline" onclick="App.selectDataSegment(null)">查看全部阶段</button></div><div class="segment-grid">'+
      (response.segments||[]).map(e=>`<button class="segment-card ${dataState.segmentId===e.id?'selected':''}" onclick="App.selectDataSegment(${e.id})">
       <b>${_esc(e.machine_name)} · 第 ${e.index} 阶段</b><small>${_esc(e.start.timestamp)} → ${_esc(e.end.timestamp)}</small>
       <span>产气：${fmt(e.start.flow)} → ${fmt(e.end.flow)} m³</span><span>电量：${fmt(e.start.power)} → ${fmt(e.end.power)} kWh</span>
       <strong>本阶段 ${UI.fmtNum(e.reduction,4)} tCO₂e · ${e.rows} 条</strong><small>${_esc(e.reason)}</small></button>`).join('')+'</div>';
}
function selectDataSegment(id) {dataState.segmentId=id;dataState.page=1;loadDataList();}

function renderDataTable(data) {
    const wrap = document.getElementById('dataTableWrap');
    if (!data.length) { wrap.innerHTML = '<p class="muted">暂无数据</p>'; return; }
    const selectable = dataState.bulkAvailable && dataState.anomaliesOnly &&
        state.projects.find(p => p.id === dataState.projectId)?.lifecycle_status === 'active';
    let html = '<table class="data-table" style="font-size:12px"><thead><tr>' +
        (selectable ? '<th><input type="checkbox" title="全选本页" onchange="App.onDataSelectAll(this.checked)"></th>' : '') +
        '<th>时间</th><th>机器</th><th>累计产气量(m³)</th><th>累计耗电量(kWh)</th>' +
        '<th>本期新增入账减排(tCO₂e)</th><th>阶段累计(tCO₂e)</th><th>全历史累计(tCO₂e)</th><th>核对</th><th>来源</th><th>操作</th>' +
        '</tr></thead><tbody>';
    let lastGroup='';
    [...data].sort((a,b)=>a.machine_id-b.machine_id||(_parseRaw(b.breakdown).segment_index||1)-(_parseRaw(a.breakdown).segment_index||1)||b.timestamp.localeCompare(a.timestamp)).forEach(d => {
        const raw = _parseRaw(d.raw_data);
        const src = { auto: '自动', manual: '手动', import: '导入', api: 'API' }[d.source] || d.source;
        const bd = _parseRaw(d.breakdown);
        const group=d.machine_id+':'+bd.segment_id;
        if (group!==lastGroup) {
            html+=`<tr class="segment-table-heading"><td colspan="${selectable?12:11}">${_esc(d.machine_name||('#'+d.machine_id))} · 第 ${bd.segment_index||1} 阶段（本页读数）</td></tr>`;
            lastGroup=group;
        }
        const flagged = bd.meter_review || bd.boundary_review;
        const reviewLabel = flagged ? (d.review_event_start === 0
            ? '区间延续·暂停计量'
            : d.review_status === 'checked_raw' ? '已核原始值·待处理'
            : d.review_status === 'needs_fix' ? '需修正'
            : bd.sampling_conflict ? '⚠ 疑似日/分钟顺序冲突' : '⚠ 首次读数跳变')
            : bd.continuity_confirmed ? '已确认停机 / 表计连续'
            : bd.reset_boundary ? (bd.segment_start?'新阶段基准':'计数切换过渡·不计量')
            : bd.coverage_gap ? '缺少中间采样·累计差计量'
            : bd.sampling_suppressed ? '汇总读数已覆盖·不重复入账'
            : bd.daily_close ? '平台日结读数'
            : (d.review_status ? '已留核对记录' : '—');
        html += `<tr>
            ${selectable ? `<td><input type="checkbox" class="data-select-row" value="${d.id}" onchange="App.onDataSelectRow(${d.id},this.checked)"></td>` : ''}
            <td>${_esc(d.timestamp)}</td>
            <td>${_esc(d.machine_name || ('#' + d.machine_id))}</td>
            <td>${raw.cumulative_flow !== undefined ? UI.fmtNum(raw.cumulative_flow, 2) : '-'}</td>
            <td>${raw.cumulative_power !== undefined ? UI.fmtNum(raw.cumulative_power, 2) : '-'}</td>
            <td>${MonitorView.amountHtml(d, 4)}</td>
            <td>${UI.fmtNum(bd.segment_cumulative, 4)}</td>
            <td>${UI.fmtNum(d.cumulative_reduction, 2)}</td>
            <td>${_esc(reviewLabel)}</td>
            <td>${_esc(src)}</td>
            <td><button class="btn btn-sm btn-outline" onclick="App.openMonitorReview(${d.id})">核对</button>
                ${state.projects.find(p => p.id === dataState.projectId)?.lifecycle_status === 'active'
                    ? `<button class="btn btn-sm btn-danger" onclick="App.deleteData(${d.id})">删除</button>` : ''}</td>
        </tr>`;
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
}

async function openMonitorReview(dataId) {
    const pid = dataState.projectId;
    const resp = await API.get(`/api/projects/${pid}/monitoring/${dataId}/review`);
    if (!resp || resp.status !== '成功') { alert((resp && resp.msg) || '读取核对资料失败'); return; }
    monitorReviewId = dataId;
    const row = resp.row, prev = resp.previous, next = resp.next;
    const raw = _parseRaw(row.raw_data), old = prev ? _parseRaw(prev.raw_data) : {};
    const bd = _parseRaw(row.breakdown);
    const minutes = prev ? Math.round((new Date(row.timestamp.replace(' ', 'T')) -
        new Date(prev.timestamp.replace(' ', 'T'))) / 60000) : null;
    const flag = bd.sampling_conflict ? '疑似日汇总与同日分钟数据顺序冲突：该段暂不计减排，需确认接口时间含义' :
        bd.meter_review ? '累计表回滚或换表：该段暂不计减排' :
        bd.boundary_review ? '缺少边界读数：该段暂不计减排' : '';
    const labels = {checked_raw:'原始读数已核实，计量问题待处理',needs_fix:'需要修正',pending:'继续待核对'};
    const reviews = (resp.reviews || []).map(e => `<li>${_esc(e.created_at)} · ${_esc(e.reviewer)}
        · ${_esc(labels[e.status] || e.status)}：${_esc(e.note)}</li>`).join('');
    const fmt = (v, digits=2) => v == null ? '—' : UI.fmtNum(v, digits);
    const project = state.projects.find(p => p.id === pid);
    document.getElementById('monitorReviewBody').innerHTML = `
        <p><b>${_esc(row.machine_name || ('机器 #' + row.machine_id))}</b> · ${_esc(row.timestamp)}
            ${flag ? '<span class="tag tag-red">' + _esc(flag) + '</span>' : ''}</p>
        ${resp.event_origin && resp.event_origin.id !== row.id ? (() => {
            const origin = resp.event_origin;
            const originRaw = _parseRaw(origin.raw_data);
            const boundary = resp.event_previous;
            const boundaryRaw = boundary ? _parseRaw(boundary.raw_data) : {};
            const flowDelta = boundaryRaw.cumulative_flow == null || originRaw.cumulative_flow == null
                ? '待核对' : fmt(originRaw.cumulative_flow - boundaryRaw.cumulative_flow);
            return `<p class="muted"><b>这条是同一次异常区间的延续。</b>首次跳变在
                ${_esc(origin.timestamp)}；此前一条 ${boundary ? _esc(boundary.timestamp) : '缺失'} 的累计产气为
                ${fmt(boundaryRaw.cumulative_flow)} m³，跳变后为 ${fmt(originRaw.cumulative_flow)} m³
                （变化 ${flowDelta} m³）。请核对首次跳变，不要把每条延续记录当作新故障。</p>`;
        })() : ''}
        <p class="muted">本期为上一条至当前条的增量；间隔 ${minutes == null ? '无上一条' : minutes + ' 分钟'}。
            长间隔记录涵盖整个间隔，不能按一分钟理解。</p>
        <div class="review-grid">
          <div><b>上一条</b><br>${prev ? _esc(prev.timestamp) : '无'}<br>
            累计产气 ${fmt(old.cumulative_flow)} m³<br>累计耗电 ${fmt(old.cumulative_power)} kWh</div>
          <div><b>当前条</b><br>${_esc(row.timestamp)}<br>
            累计产气 ${fmt(raw.cumulative_flow)} m³<br>累计耗电 ${fmt(raw.cumulative_power)} kWh</div>
          <div><b>本期计算</b><br>
            产气增量 ${fmt(bd.delta_flow_m3)} m³<br>耗电增量 ${fmt(bd.delta_power_kwh)} kWh<br>
            基准线 ${fmt(bd.baseline_emissions_tco2, 6)} − 项目排放 ${fmt(bd.project_emissions_tco2, 6)}
            = <b>${fmt(row.carbon_reduction, 6)} tCO₂e</b></div>
        </div>
        <p class="muted">公式参数：Zᵦ=${fmt(bd.z_b_factor, 4)} × 10⁻⁵ tCO₂/m³；
            电网因子=${fmt(bd.ef_grid, 4)} tCO₂/MWh。
            平台 apartValue（单次接口采样增量）：产气 ${fmt(raw.apart_flow)} m³、
            耗电 ${fmt(raw.apart_power)} kWh；不一定覆盖以上两条记录间的完整区间。</p>
        ${next ? `<p class="muted">下一条：${_esc(next.timestamp)}，可用来核对读数回滚后的走势。</p>` : ''}
        <details><summary>查看原始数据与计算明细</summary>
          <pre class="review-json">${_esc(JSON.stringify(raw, null, 2))}</pre>
          <pre class="review-json">${_esc(JSON.stringify(bd, null, 2))}</pre></details>
        <p><b>核对方法：</b>在源数据平台查同一气站、同一时间的累计值，核实单位、表号和两测点时间。
            换表或回滚需记录事件及边界读数，由技术人员修正。保存核对记录不会自动改减排值。</p>
        <h4>核对记录</h4><ul>${reviews || '<li>尚无核对记录</li>'}</ul>
        ${project && project.lifecycle_status === 'active' ? `<div class="review-form">
          <input class="input" id="monitorReviewer" maxlength="80" placeholder="核对人（必填）">
          <select class="input" id="monitorReviewStatus">
            <option value="pending">继续待核对</option>
            <option value="checked_raw">原始读数已核实，计量问题待处理</option>
            <option value="needs_fix">需要修正数据或表计边界</option>
          </select>
          <textarea class="input" id="monitorReviewNote" maxlength="2000" rows="3"
            placeholder="写明核对来源、原平台读数、表号或换表日期（必填）"></textarea></div>` :
            '<p class="muted">项目已作废，历史核对记录只读。</p>'}
    `;
    document.getElementById('btnSaveMonitorReview').style.display =
        project && project.lifecycle_status === 'active' ? '' : 'none';
    document.getElementById('monitorReviewModal').classList.remove('hidden');
}

function closeMonitorReview() {
    monitorReviewId = null;
    document.getElementById('monitorReviewModal').classList.add('hidden');
}

async function saveMonitorReview() {
    if (!monitorReviewId) return;
    const reviewer = document.getElementById('monitorReviewer').value.trim();
    const review_status = document.getElementById('monitorReviewStatus').value;
    const note = document.getElementById('monitorReviewNote').value.trim();
    if (!reviewer || !note) { alert('请填写核对人和核对依据'); return; }
    const resp = await API.post(`/api/projects/${dataState.projectId}/monitoring/${monitorReviewId}/review`,
        { reviewer, review_status, note });
    if (!resp || resp.status !== '成功') { alert((resp && resp.msg) || '保存失败'); return; }
    const id = monitorReviewId;
    await openMonitorReview(id);
    await loadDataList();
}

const qualityState = { cases: [], selected: -1, preview: null, timer:null };

async function openQualityModal() {
    if (!dataState.projectId) return;
    document.getElementById('qualityModal').classList.remove('hidden');
    document.getElementById('qualityCaseList').innerHTML = '<p class="muted">正在归并机器异常事件…</p>';
    const query = dataState.machineId ? '?machine_id=' + dataState.machineId : '';
    try {
        const response = await API.get('/api/projects/' + dataState.projectId + '/monitoring/quality' + query);
        if (response.status!=='成功') throw new Error(response.msg||'事件读取失败');
        qualityState.cases = response.cases || [];
        qualityState.selected = -1;
        qualityState.preview = null;
        renderQualityCaseList();
        document.getElementById('qualityCaseDetail').innerHTML =
            '<p class="muted">选择左侧事件查看读数和处理方式。</p>';
    } catch (error) {
        document.getElementById('qualityCaseList').innerHTML =
            '<p class="quality-warning">' + _esc(error.message || '事件读取失败') + '</p>';
    }
}
function closeQualityModal() {
    if (qualityState.timer) {clearTimeout(qualityState.timer);qualityState.timer=null;}
    document.getElementById('qualityModal').classList.add('hidden');
    qualityState.preview = null;
}
function renderQualityCaseList() {
    const names = {meter_drop:'累计读数回退', sampling_overlap:'日结与细粒度交叠',
                   coverage_gap:'监测日期缺口', apart_mismatch:'增量与累计差不符'};
    const statuses = {pending:'连续性待确认',resolved:'已留处理结论',auto_resolved:'自动去重',diagnostic:'来源诊断',auto_segmented:'已自动分段 / 去错点',stale:'原确认已失效'};
    const el = document.getElementById('qualityCaseList');
    if (!qualityState.cases.length) {
        el.innerHTML = '<p class="muted">此范围暂未发现计量回退、交叠或超过三天的缺口。</p>';
        return;
    }
    el.innerHTML = qualityState.cases.map((e, i) =>
        '<button class="quality-case-item ' + (qualityState.selected === i ? 'active' : '') +
        '" onclick="App.selectQualityCase(' + i + ')"><b>' + _esc(e.machine_name) +
        '</b> · ' + _esc(names[e.kind] || e.kind) + '<br><small>' +
        _esc(e.start) + (e.end !== e.start ? ' — ' + _esc(e.end) : '') +
        '</small><br><small>' + _esc(statuses[e.status] || e.status) +
        ' · 关联 ' + e.affected_count + ' 条读数</small></button>').join('');
}
function selectQualityCase(index) {
    if (qualityState.timer) {clearTimeout(qualityState.timer);qualityState.timer=null;}
    const e = qualityState.cases[index];
    if (!e) return;
    qualityState.selected = index;
    qualityState.preview = null;
    renderQualityCaseList();
    const active = state.projects.find(p => p.id === dataState.projectId)?.lifecycle_status === 'active';
    const names = {meter_drop:'累计读数回退', sampling_overlap:'日结与细粒度交叠',
                   coverage_gap:'监测日期缺口', apart_mismatch:'增量与累计差不符'};
    const fmt = v => v == null ? '—' : UI.fmtNum(v, 2);
    const before = e.before || {};
    const candidates = (e.candidate_points || []).map(p =>
        '<option value="' + p.id + '">' + _esc(p.timestamp) + ' · 产气 ' +
        fmt(p.flow) + ' m³ / 用电 ' + fmt(p.power) + ' kWh</option>').join('');
    let actions = '';
    if (e.kind === 'meter_drop') {
        actions = '<label>核对结论</label><select class="input" id="qualityResolution" onchange="App.clearQualityPreview()">' +
          '<option value="pending">人工要求暂停本事件计量</option>' +
          '<option value="reset_epoch">确认换表或重新计数，从新起点续算</option>' +
          (e.recovered ? '<option value="exclude_event">确认中间读数无效，跨过它们计算</option>' : '') +
          '</select><label>新计量起点（仅确认换表使用）</label>' +
          '<select class="input" id="qualityBaseline" onchange="App.clearQualityPreview()">' +
          (candidates || '<option value="">暂无候选读数</option>') + '</select>';
    } else if (e.kind === 'coverage_gap') {
        actions = '<label>缺口核对结论</label><select class="input" id="qualityResolution" onchange="App.clearQualityPreview()">' +
          '<option value="pending">继续待核对</option>' +
          (e.decision_stale?'<option value="accept_new_data">已核对新补入读数，按新数据重算</option>':'') +
          '<option value="confirmed_shutdown">确认停机、表计连续（按 C－B 计量）</option>' +
          '<option value="confirmed_continuous">其他原因，确认表计连续（按 C－B 计量）</option>' +
          '<option value="start_epoch">确认无法承接，从 C 建立新阶段</option>' +
          '<option value="confirmed_inactive">首条前未投运 / 无计量起点</option>' +
          '<option value="confirmed_missing">已核实接口缺数，仍待补齐</option></select>';
    }
    const decision = e.decision || {};
    const dropInfo = e.kind === 'meter_drop'
      ? '<div class="review-grid"><div><b>回退前</b><br>' +
        _esc(before.timestamp || '—') + '<br>产气 ' + fmt(before.flow) +
        ' m³<br>用电 ' + fmt(before.power) + ' kWh</div><div><b>回退位置</b><br>' +
        (e.candidate_points || []).map(p => _esc(p.timestamp) + '：产气 ' +
            fmt(p.flow) + ' / 用电 ' + fmt(p.power)).join('<br>') +
        '</div><div><b>后续情况</b><br>' +
        (e.recovered ? '两项累计值已超过回退前读数' : '未确认恢复到原计量范围') +
        '<br>需核实表号、站点与来源。</div></div>' : '';
    const overlapInfo = e.kind === 'sampling_overlap'
      ? '<p>已识别为日结口径的读数标为当天 00:00，其值代表当天末尾；该口径须与源平台约定一致。账本保留细粒度原始行，由日结行代表当天，避免重复计量。</p>' : '';
    const gapInfo=e.kind==='coverage_gap' ? `<div class="review-grid"><div><b>缺口前 B</b><br>${_esc(e.before?.timestamp||'无起点')}<br>产气 ${fmt(e.before?.flow)} m³<br>电量 ${fmt(e.before?.power)} kWh</div><div><b>缺口后 C</b><br>${_esc(e.after?.timestamp||e.end)}<br>产气 ${fmt(e.after?.flow)} m³<br>电量 ${fmt(e.after?.power)} kWh</div><div><b>实测增量及速率</b><br>${UI.fmtNum(e.metrics?.days,3)} 天<br>产气 ${fmt(e.metrics?.delta?.flow)} m³ / ${fmt(e.metrics?.rate_per_day?.flow)} m³/天<br>电量 ${fmt(e.metrics?.delta?.power)} kWh / ${fmt(e.metrics?.rate_per_day?.power)} kWh/天</div></div>
       <p>判断：${_esc(({possible_shutdown:'可能停机或低负荷，低增量不清零',within_reference:'增量在历史参考范围内',high_rate:'增量高于复查线，建议定向回查',rollback:'累计表回退，应重新计数',insufficient_history:'历史样本不足，不作统计判真',initial_boundary:'首条前无基准读数'})[e.metrics?.classification]||'待核对')}。</p>
       <details><summary>历史参考范围（按真实秒数折算日速率）</summary><pre class="review-json">${_esc(JSON.stringify(e.metrics?.reference||{},null,2))}</pre></details>
       ${e.before?'<button class="btn btn-outline" onclick="App.recheckQualityGap()">定向回查 B—C（日 / 小时 / 细粒度）</button>':''}<div id="qualityProbeResult"></div>`:'';
    const saved = decision.resolution && decision.resolution !== 'pending'
      ? '<p>已记录结论：' + _esc(decision.resolution) + '；' +
        _esc(decision.reviewer || '') + '：' + _esc(decision.note || '') +
        '<br>证据：' + _esc(decision.evidence || '') + '</p>' : '';
    const form = active && actions ? '<div class="review-form">' + actions +
      '<label>核对人</label><input class="input" id="qualityReviewer" maxlength="80" placeholder="姓名或工号">' +
      '<label>原始证据来源（停机 / 连续性确认可留空）</label><input class="input" id="qualityEvidence" maxlength="2000" placeholder="现场记录、工单、表号等">' +
      '<label>处理依据</label><textarea class="input" id="qualityNote" maxlength="2000" placeholder="说明为何认定为换表、错误读数或缺数"></textarea>' +
      '<div style="display:flex;gap:8px;margin-top:10px"><button class="btn btn-outline" onclick="App.previewQualityCase()">预览差异</button>' +
      '<button class="btn btn-primary" id="qualityApplyBtn" onclick="App.applyQualityCase()" disabled>按预览应用结论</button></div>' +
      '<div id="qualityPreview"></div></div>' :
      '<p class="muted">此事件只读；原始记录及核对结论仍可查看。</p>';
    document.getElementById('qualityCaseDetail').innerHTML =
      '<h3>' + _esc(names[e.kind] || e.kind) + ' · ' + _esc(e.machine_name) +
      '</h3><p>' + _esc(e.start) + ' — ' + _esc(e.end) + '；涉及 ' +
      e.affected_count + ' 条原始读数。</p><p class="quality-warning">' +
      _esc(e.detail) + '</p>' + (e.decision_stale?'<p class="quality-warning">原确认后增加了中间读数，原确认已失效，请重新预览。</p>':'') + dropInfo + gapInfo + overlapInfo + saved + form + '<details><summary>历次确认 / 撤销记录</summary><pre class="review-json">'+_esc(JSON.stringify(e.history||[],null,2))+'</pre></details>';
    if (!decision.resolution && e.effective_resolution && document.getElementById('qualityResolution')) {
        document.getElementById('qualityResolution').value=e.effective_resolution;
        if (e.effective_baseline && document.getElementById('qualityBaseline')) document.getElementById('qualityBaseline').value=e.effective_baseline;
    }
    if (e.probe) renderQualityProbe(e.probe);
    if (decision.resolution && document.getElementById('qualityResolution')) {
        document.getElementById('qualityResolution').value = decision.resolution;
        if (decision.baseline_row_id && document.getElementById('qualityBaseline'))
            document.getElementById('qualityBaseline').value = decision.baseline_row_id;
    }
}
function clearQualityPreview() {
    qualityState.preview = null;
    const btn = document.getElementById('qualityApplyBtn');
    if (btn) btn.disabled = true;
    const panel = document.getElementById('qualityPreview');
    if (panel) panel.innerHTML = '';
}
function qualityPayload() {
    const e = qualityState.cases[qualityState.selected];
    const resolution = document.getElementById('qualityResolution')?.value || 'pending';
    return { machine_id:e.machine_id, kind:e.kind, start_row_id:e.start_row_id,
             resolution:resolution, baseline_row_id:resolution === 'reset_epoch'
                 ? document.getElementById('qualityBaseline')?.value : null };
}
async function previewQualityCase() {
    const panel = document.getElementById('qualityPreview');
    panel.innerHTML = '<p class="muted">正在重算预览…</p>';
    const response = await API.post('/api/projects/' + dataState.projectId +
        '/monitoring/quality/preview', qualityPayload());
    if (!response || response.status !== '成功') {
        panel.innerHTML = '<p class="quality-warning">' +
            _esc(response?.msg || '预览失败') + '</p>';
        return;
    }
    qualityState.preview = response.preview;
    const p = response.preview;
    const years = Object.entries(p.annual_delta || {}).map(([year, delta]) =>
        year + '：' + UI.fmtNum(delta, 6) + ' tCO₂e').join('；') || '无金额变化';
    panel.innerHTML = '<div class="quality-impact"><b>修复前后差异</b><br>' +
      '受影响账本行 ' + p.rows_changed + ' 条；机器减排变化 ' +
      UI.fmtNum(p.reduction_delta, 6) + ' tCO₂e。<br>年度变化：' +
      _esc(years) + '。<br>已生成报告 ' + p.existing_reports.length +
      ' 份，应用后需逐份核查影响。<details><summary>查看部分行差异</summary>' +
      '<pre class="review-json">' + _esc(JSON.stringify(
        (p.sample_changes || []).map(c => ({
            time:c.timestamp, old:c.old_reduction, next:c.new_reduction
        })), null, 2)) + '</pre></details></div>';
    document.getElementById('qualityApplyBtn').disabled = false;
}
async function applyQualityCase() {
    if (!qualityState.preview) return;
    const payload = {...qualityPayload(),
        signature:qualityState.preview.signature,
        reviewer:document.getElementById('qualityReviewer').value.trim(),
        evidence:document.getElementById('qualityEvidence').value.trim(),
        note:document.getElementById('qualityNote').value.trim()};
    const continuous=['confirmed_shutdown','confirmed_continuous'].includes(payload.resolution);
    if (!payload.reviewer || !payload.note || (!continuous && !['pending','accept_new_data'].includes(payload.resolution) && !payload.evidence)) {
        alert('请填写核对人和处理原因；换表或排除错误点还需证据来源。'); return;
    }
    const response = await API.post('/api/projects/' + dataState.projectId +
        '/monitoring/quality/apply', payload);
    if (!response || response.status !== '成功') {
        alert(response?.msg || '应用失败，请重新预览'); clearQualityPreview(); return;
    }
    await openQualityModal();
    await loadDataList();
}

function renderQualityProbe(probe) {
    const el=document.getElementById('qualityProbeResult');if (!el) return;
    const status=probe.job_status||probe.status;
    const failed=(probe.chunks||[]).filter(c=>c.status==='error').length;
    const statusText={queued:'排队中',running:'回查中',ready:'预览已就绪',applied:'已补入',failed:'失败',interrupted:'服务重启后中断'}[status]||status;
    el.innerHTML=`<p>回查状态：${_esc(statusText)}。${_esc(probe.summary||probe.error||'')}</p>`+
      (failed?`<p class="quality-warning">仍有 ${failed} 个时间块请求失败，本次回查不完整；可查看下方失败原因。</p>`:'')+
      (probe.current?.status==='error'&&probe.current.retry_at?`<p>接口请求失败，等待至 ${_esc(probe.current.retry_at)} 再试（已请求 ${probe.current.attempts}/3 次）。</p>`:'')+
      (probe.preview?`<p>可补 ${probe.preview.inserted} 条；减排 ${UI.fmtNum(probe.preview.old_total,6)} → ${UI.fmtNum(probe.preview.new_total,6)} tCO₂e。</p>`:'')+
      `<details><summary>各时间块结果</summary><pre class="review-json">${_esc(JSON.stringify(probe.chunks||[],null,2))}</pre></details>`+
      ((probe.status==='ready'||probe.job_status==='ready')?`<button class="btn btn-outline" onclick="App.refreshQualityProbe('${_esc(probe.id)}')">刷新此回查预览</button>`:'')+
      ((probe.status==='ready'||probe.job_status==='ready')&&probe.preview?.inserted>0?`<button class="btn btn-primary" onclick="App.applyQualityProbe('${_esc(probe.id)}')">按此预览补入并重算</button>`:'');
    if (['running','queued'].includes(probe.job_status||probe.status)) {
        clearTimeout(qualityState.timer);qualityState.timer=setTimeout(()=>pollQualityProbe(probe.id),2500);
    }
}
async function pollQualityProbe(id) {
    if (document.getElementById('qualityModal').classList.contains('hidden')) return;
    const r=await API.get(`/api/projects/${dataState.projectId}/monitoring/quality/probes/${id}`);
    if (r.status==='成功') renderQualityProbe({id,status:r.job_status,...r.result});
}
async function recheckQualityGap() {
    const e=qualityState.cases[qualityState.selected];
    const r=await API.post(`/api/projects/${dataState.projectId}/monitoring/quality/recheck`,{machine_id:e.machine_id,start_row_id:e.start_row_id});
    if (r.status!=='成功') return alert(r.msg||'回查失败');
    renderQualityProbe({id:r.job_id,status:'queued'});
}
async function refreshQualityProbe(id) {
    const r=await API.post(`/api/projects/${dataState.projectId}/monitoring/quality/probes/${id}`,{action:'refresh'});
    if (r.status!=='成功') return alert(r.msg||'刷新预览失败');
    renderQualityProbe({id,status:r.job_status,...r.result});
}
async function applyQualityProbe(id) {
    const reviewer=document.getElementById('qualityReviewer')?.value.trim();
    const note=document.getElementById('qualityNote')?.value.trim();
    if (!reviewer||!note) return alert('请先填写操作人和处理原因。');
    if (!confirm('确认按当前预览补入缺失读数并重算？系统会先备份，原有读数不覆盖。')) return;
    const r=await API.post(`/api/projects/${dataState.projectId}/monitoring/quality/probes/${id}`,{reviewer,note});
    if (r.status!=='成功') return alert(r.msg||'应用失败');
    await openQualityModal();await loadDataList();await loadProjects();
}

function renderDataPager(total) {
    const el = document.getElementById('dataPager');
    const pages = Math.max(1, Math.ceil(total / dataState.pageSize));
    let html = `<button class="btn btn-sm btn-outline" ${dataState.page <= 1 ? 'disabled' : ''} onclick="App.gotoDataPage(${dataState.page - 1})">上一页</button>`;
    html += `<span class="muted">第 ${dataState.page} / ${pages} 页</span>`;
    html += `<button class="btn btn-sm btn-outline" ${dataState.page >= pages ? 'disabled' : ''} onclick="App.gotoDataPage(${dataState.page + 1})">下一页</button>`;
    html += `<span style="display:flex;align-items:center;gap:6px;margin-left:10px">跳至
        <input type="number" id="dataPageInput" class="input" style="width:70px" min="1" max="${pages}" value="${dataState.page}"
               onkeydown="if(event.key==='Enter')App.jumpDataPage()">
        页 <button class="btn btn-sm btn-outline" onclick="App.jumpDataPage()">跳转</button></span>`;
    el.innerHTML = html;
}

function jumpDataPage() {
    const input = document.getElementById('dataPageInput');
    if (!input) return;
    let v = parseInt(input.value, 10);
    const pages = Math.max(1, Math.ceil(dataState.total / dataState.pageSize));
    if (isNaN(v) || v < 1) v = 1;
    if (v > pages) v = pages;
    dataState.page = v;
    loadDataList();
}

function gotoDataPage(page) {
    dataState.page = page;
    loadDataList();
}

function updateDataSelection() {
    const active = state.projects.find(p => p.id === dataState.projectId)?.lifecycle_status === 'active';
    const show = !!(active && dataState.bulkAvailable && dataState.anomaliesOnly);
    const bulk = document.getElementById('dataBulkDeleteBtn');
    bulk.style.display = show ? '' : 'none';
    bulk.disabled = !dataState.selected.size;
    bulk.textContent = `批量删除所选（${dataState.selected.size}）`;
    document.getElementById('dataRestoreBatchBtn').style.display =
        active && dataState.bulkAvailable ? '' : 'none';
}

function onDataSelectAll(checked) {
    document.querySelectorAll('#dataTableWrap .data-select-row').forEach(box => {
        box.checked = checked;
        if (checked) dataState.selected.add(Number(box.value));
        else dataState.selected.delete(Number(box.value));
    });
    updateDataSelection();
}

function onDataSelectRow(id, checked) {
    if (checked) dataState.selected.add(id);
    else dataState.selected.delete(id);
    updateDataSelection();
}

async function bulkDeleteData() {
    const rows = dataState.visibleRows.filter(row => dataState.selected.has(row.id));
    if (!rows.length) return;
    const machines = [...new Set(rows.map(row => row.machine_name || ('机器 #' + row.machine_id)))];
    const message = `将删除本页勾选的 ${rows.length} 条待核对记录（${machines.join('、')}）。
原始记录会留在可恢复档案，删除后重新计算对应机器的减排量。请先确认勾选的是错误读数，而非同一次跳变的正常延续记录。
确定继续？`;
    if (!confirm(message)) return;
    const resp = await API.post(`/api/projects/${dataState.projectId}/monitoring/bulk-delete`,
                                {ids: rows.map(row => row.id)});
    if (resp.status !== '成功') { alert((resp && resp.msg) || '批量删除失败'); return; }
    alert(`已归档并删除 ${resp.deleted} 条。可用“恢复上次批量删除”撤回。`);
    await loadDataList();
    await loadProjects();
}

async function restoreLastDataBatch() {
    const pid = dataState.projectId;
    const resp = await API.get(`/api/projects/${pid}/monitoring/deleted-batches`);
    const batch = (resp.batches || [])[0];
    if (!batch) { alert('没有待恢复的批量删除记录'); return; }
    if (!confirm(`恢复 ${batch.deleted_at} 删除的 ${batch.row_count} 条记录？恢复后会重新计算减排量。`)) return;
    const result = await API.post(`/api/projects/${pid}/monitoring/deleted-batches/${batch.batch_id}/restore`, {});
    if (result.status !== '成功') { alert((result && result.msg) || '恢复失败'); return; }
    alert(`已恢复 ${result.restored} 条记录。`);
    await loadDataList();
    await loadProjects();
}

async function deleteData(dataId) {
    if (!confirm('确定删除这条监测数据？删除后项目累计会自动重算。')) return;
    const resp = await API.del(`/api/projects/${dataState.projectId}/monitoring/${dataId}`);
    if (resp.status === '成功') {
        loadDataList();
        loadProjects();  // 刷新项目累计显示
    } else {
        alert('删除失败: ' + (resp.msg || '未知错误'));
    }
}

function drawDataChart(data) {
    const canvas = document.getElementById('dataChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    if (!data.length) { return; }
    // 数据按时间升序（接口返回倒序，这里反转）
    const arr = [...data].reverse();
    const flow = arr.map(d => _parseRaw(d.raw_data).cumulative_flow);
    const power = arr.map(d => _parseRaw(d.raw_data).cumulative_power);
    const cr = arr.map(d => d.carbon_reduction || 0);
    // 三个指标量纲差很大，分别归一化到 0..1 画三条线
    const series = [
        { name: '产气量', values: flow, color: '#42B983' },
        { name: '耗电量', values: power, color: '#4A90D9' },
        { name: '本期减排', values: cr, color: '#E67E22' },
    ];
    const padding = 50;
    const plotW = W - padding * 2, plotH = H - padding * 2;
    ctx.strokeStyle = '#eee'; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = padding + plotH * i / 4;
        ctx.beginPath(); ctx.moveTo(padding, y); ctx.lineTo(W - padding, y); ctx.stroke();
    }
    series.forEach(s => {
        const vals = s.values;
        if (!vals.length) return;
        const maxV = Math.max(...vals.filter(v => v != null), 1);
        ctx.strokeStyle = s.color; ctx.lineWidth = 2; ctx.beginPath();
        vals.forEach((v, i) => {
            if (v == null) return;
            const x = padding + plotW * i / Math.max(1, vals.length - 1);
            const y = padding + plotH * (1 - (v / maxV));
            if (i === 0 || vals[i - 1] == null || arr[i].machine_id!==arr[i-1].machine_id || _parseRaw(arr[i].breakdown).segment_id!==_parseRaw(arr[i-1].breakdown).segment_id) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
    });
    // 图例
    let lx = padding;
    series.forEach(s => {
        ctx.fillStyle = s.color;
        ctx.fillRect(lx, 8, 12, 12);
        ctx.fillStyle = '#666'; ctx.font = '12px sans-serif';
        ctx.fillText(s.name, lx + 16, 18);
        lx += 16 + ctx.measureText(s.name).width + 20;
    });
}

// 暴露到全局
window.App = {
    machineVoid, machineRestore, machineHistory,
    voidProject, restoreProject, retryProjectArchive, showProjectArchive,
    editProject(id) {
        const p = state.projects.find(x => x.id === id);
        if (p) UI.openProjectModal(p);
    },
    openManualData(id, mid) { UI.openManualModal(id, mid); },
    closeManualModal() { UI.closeManualModal(); },
    // 历史数据导入（机器行「导入」按钮打开的弹窗）
    openImportModal, closeImportModal,
    // 断档兜底（检测 → 人工确认 → 执行）
    openGapList, closeGapListModal,
    openGapConfirm, closeGapConfirmModal, recalcGapPreview, confirmGapApply,
    // 历史数据批量回溯（全局 / 单项目）
    openBackfillModal, closeBackfillModal, startBackfill,
    // 数据查看（折线图 + 分页表格 + 删除）
    openDataModal, closeDataModal, onDataMachineChange, onDataAnomaliesChange,
    openMonitorReview, closeMonitorReview, saveMonitorReview,
    selectDataSegment, recheckQualityGap, refreshQualityProbe, applyQualityProbe,
    openQualityModal, closeQualityModal, selectQualityCase,
    clearQualityPreview, previewQualityCase, applyQualityCase,
    gotoDataPage, deleteData, bulkDeleteData, restoreLastDataBatch,
    onDataSelectAll, onDataSelectRow, jumpDataPage,
    // 机器（多设备）增删改，全部由项目弹窗里的内联按钮触发
    machineAdd, machineSave, machineDelete, machineToggleEdit, machineCancelEdit, machineBackfillSourceToggle, machineNormalizerSelected, machineApiTest,
    machineSourceToggle, machineNormToggle, machineBackfillToggle,
    openSiteListModal, closeSiteListModal, selectSite, refreshSites, filterSites,
    autoResizeTA,
    syncProject, syncAllProjects, deleteProject, generateReport, openReportForProject, collectNow,
    vcerSelectStage, submitVCERStage, vcerSelectRound,
    showVCERKeyModal, openVCERPwdModal, closeVCERPwdModal,
    closeVCERKeyModal,
    syncVcerCodes, reserveCode,
    loadReportsTab, loadVariableReference, loadReportHistory, setReportHistoryMode, downloadReport: autoDownload,
    toggleCollapsible,
    loadPresets, selectPreset, savePresetValue, deletePreset, richTextCmd,
    renderPresetList, renderProjectPresetList,
    // 项目预设
    loadProjectPresets, selectProjectPreset, saveProjectPresetValue, deleteProjectPreset,
    openProjectPresetModal, saveProjectPreset,
    // 全项目通用预设
    loadGlobalPresets, renderGlobalPresetList, selectGlobalPreset,
    saveGlobalPresetValue, deleteGlobalPreset, openGlobalPresetModal, saveGlobalPreset,
    globalPresetImageUpload,
    // 表格预设编辑
    tableToggleSrc, tableAddRow, tableDelRow, tableAddCol, tableDelCol,
    // 预设批量编辑（Excel）
    openPresetBatchModal, closePresetBatchModal, presetBatchToggleAll,
    exportPresetBatch, importPresetBatch,
    // 预设折叠 & 拖拽
    togglePresetCat, presetDragStart, presetDragEnd,
    presetZoneDragOver, presetZoneDragLeave, presetZoneDrop,
    presetItemDragOver, presetItemDragLeave, presetItemDrop,
    presetCatDragOver, presetCatDragLeave, presetCatDrop,
    catDragStart, catDragEnd,
    // 变量搜索
    filterPlaceholders, switchVarTab,
    copyVarPath, exportVariableReference, showToast, runConsoleCode,
    richTextInsertImage, presetImageUpload, projectPresetImageUpload,
    openPresetModal, closePresetModal,
};

// 启动
document.addEventListener('DOMContentLoaded', function() {
    init();
    // 初始化所有自定义下拉选择框
    setTimeout(() => CustomSelect.initAll(document), 100);
});

})();
