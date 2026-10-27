/* 实时监测展示层：中文说明、按机器分线；不改动计量账本。 */
const MonitorView = (() => {
    const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const number = value => value === null || value === undefined || value === '' || typeof value === 'boolean' ? null : (Number.isFinite(Number(value)) ? Number(value) : null);
    const format = (value, digits = 4) => number(value) === null ? '—' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits});
    const parse = value => { try { return typeof value === 'object' && value !== null ? value : JSON.parse(value || '{}'); } catch { return {}; } };
    const stamp = value => { const time = new Date(String(value || '').replace(' ', 'T')).getTime(); return Number.isFinite(time) ? time : null; };
    const palette = ['#16866d', '#4d7bc8', '#c78a32', '#926bb8', '#cc6b76', '#467d91'];
    const labels = {
        delta_flow_m3:['本期产气量','m³'], delta_power_kwh:['本期耗电量','kWh'],
        z_b_factor:['基准线排放系数','10⁻⁵ tCO₂/m³'], baseline_emissions_tco2:['基准线排放量','tCO₂'], project_emissions_tco2:['项目实际排放量','tCO₂'],
        ef_grid:['电网排放因子','tCO₂e/MWh'], delta_generation_kwh:['本期发电量','kWh'], delta_generation_mwh:['本期发电量','MWh'],
        delta_energy_kwh:['本期实际用电量','kWh'], delta_energy_mwh:['本期实际用电量','MWh'], baseline_efficiency:['基准效率',''], actual_efficiency:['实际效率',''], efficiency_ratio:['效率比值',''],
        energy_saved_kwh:['本期节电量','kWh'], energy_saved_mwh:['本期节电量','MWh'], power_saved_kw:['节省功率','kW'], delta_hours:['本期运行时长','小时'],
        delta_heat_mj:['本期热量','MJ'], delta_heat_gj:['本期热量','GJ'], delta_electricity_kwh:['本期耗电量','kWh'], delta_steam_tons:['本期蒸汽量','吨'],
        steam_enthalpy:['蒸汽焓值','GJ/吨'], old_emission_intensity:['改造前排放强度','tCO₂e/GJ'], new_emission_intensity:['改造后排放强度','tCO₂e/GJ'], hp_emission_intensity:['热泵排放强度','tCO₂e/GJ'],
        ef_fossil:['化石燃料排放因子','tCO₂e/GJ'], ef_biomass:['生物质排放因子','tCO₂e/GJ'], ef_fuel:['燃料排放因子','tCO₂e/GJ'], ef_grid_gj:['电网排放因子','tCO₂e/GJ'],
        cop_nominal:['额定性能系数',''], cop_observed:['实测性能系数',''], boiler_efficiency:['锅炉效率',''], fuel_displaced_gj:['替代燃料热量','GJ'], ef_thermal:['热力排放因子','tCO₂e/GJ'],
    };
    const flags = {meter_review:'累计表读数待核对', boundary_review:'计量边界待核对', sampling_conflict:'采样时间存在冲突', sampling_suppressed:'重叠采样未重复计量', excluded_invalid_reading:'已排除无效读数', reset_boundary:'新计量阶段基准', minor_rollback:'存在小幅读数修正', coverage_gap:'存在采样缺口', continuity_confirmed:'表计连续性已确认', continuity_change:'确认后新增读数，待复核'};
    const selectedMachineByProject = new Map();
    let metric = 'carbon_reduction';
    let observer = null;
    const machineName = (id, machines) => {
        const item = machines.find(machine => String(machine.id) === String(id));
        return item ? (item.machine_name || item.name || `机器 ${id}`) : (id == null ? '未关联机器' : `机器 ${id}`);
    };
    function readingStatus(row) {
        const bd = parse(row.breakdown);
        const value = number(row.carbon_reduction);
        if (value !== null && value !== 0) return {kind:'booked', label:'正常入账', counted:true};
        if (bd.sampling_suppressed) return {kind:'merged', label:'已合并计量', counted:false,
            detail:'这条采样已由日／小时汇总读数覆盖，减排量已记在汇总记录；本条新增入账为 0，不代表该时段没有减排。'};
        if (bd.excluded_invalid_reading) return {kind:'excluded', label:'无效读数不计量', counted:false,
            detail:'原始记录保留供追溯，已排除在有效计量范围外。'};
        if (bd.meter_review || bd.boundary_review) return {kind:'review', label:'待核对，暂不计量', counted:false,
            detail:'该条存在表计或边界问题，尚不能将账面零值解释为实际零减排；请到数据核对窗口查看。'};
        if (bd.baseline || bd.reset_boundary || bd.segment_start) return {kind:'baseline', label:'计量起点', counted:false,
            detail:'这条是首条或新阶段的起点，不计算跨边界差额；后续有效读数继续计算。'};
        if (value === null) return {kind:'missing', label:'暂无计算结果', counted:false, detail:'缺少有效的减排计算值。'};
        const baseline = number(bd.baseline_emissions_tco2), actual = number(bd.project_emissions_tco2);
        return {kind:'zero', label:'本期新增减排为零', counted:true,
            detail:baseline !== null && actual !== null && baseline <= actual
                ? '本期基准线排放不高于项目实际排放，按公式净减排下限为 0。'
                : '该条有效计算结果为零；请结合本期产气、耗电增量及核对状态查看原因。'};
    }
    function amountHtml(row, digits=4) {
        const status = readingStatus(row), value = number(row.carbon_reduction);
        if (!status.counted) return `<span class="monitor-booking-status monitor-booking-${status.kind}" title="${escape(status.detail)}">${status.label}</span>`;
        const text = value > 0 && value < 10 ** -digits ? '&lt;' + (10 ** -digits).toFixed(digits) : format(value,digits);
        return `<span title="${escape(status.detail || ('本条新增入账减排量：' + format(value,6) + ' tCO₂e'))}">${text}</span>`;
    }
    function breakdownHtml(bd) {
        const rows = Object.entries(labels).filter(([key]) => bd[key] !== undefined).map(([key, [label, unit]]) => {
            const explicitUnit = bd[key.replace(/_factor$/, '') + '_unit'] || (key === 'z_b_factor' ? bd.z_b_unit : '');
            return `<div class="monitor-calc-item"><dt>${label}</dt><dd>${format(bd[key], key.startsWith('delta_') ? 2 : 6)} <small>${escape(explicitUnit || unit)}</small></dd></div>`;
        }).join('');
        const badges = Object.entries(flags).filter(([key]) => bd[key]).map(([, text]) => `<span class="monitor-quality-tag">${text}</span>`).join('');
        return `${badges ? `<div class="monitor-quality-tags">${badges}</div>` : ''}
            ${bd.note ? `<p class="monitor-calc-note">${escape(bd.note)}</p>` : ''}
            ${rows ? `<dl class="monitor-calc-grid">${rows}</dl>` : '<p class="monitor-empty">该条记录暂无可展示的计算分解。</p>'}
            ${bd.formula ? `<div class="monitor-formula"><span>${bd.sampling_suppressed ? '原记录计算式（不再次入账）' : '原记录保存的计算式'}</span><code>${escape(bd.formula)}</code></div>` : ''}
            <details class="monitor-technical"><summary>查看技术明细（原始字段）</summary><pre>${escape(JSON.stringify(bd, null, 2))}</pre></details>`;
    }
    function prepareSeries(rows, key, machines) {
        const groups = new Map();
        for (const row of rows) {
            const time = stamp(row.timestamp);
            if (time === null) continue;
            const id = row.machine_id == null ? 'unassigned' : String(row.machine_id);
            if (!groups.has(id)) groups.set(id, {id, name: machineName(row.machine_id, machines), points: []});
            groups.get(id).points.push({time, value: key === 'carbon_reduction' && !readingStatus(row).counted ? null : number(row[key]), timestamp: row.timestamp, segment: parse(row.breakdown).segment_id});
        }
        return [...groups.values()].map((group, index) => ({...group, color: palette[index % palette.length], points: group.points.sort((a,b) => a.time-b.time)}));
    }
    function renderChart(host, rows, machines) {
        const groups = prepareSeries(rows, metric, machines);
        const points = groups.flatMap(group => group.points.filter(point => point.value !== null));
        const legend = host.parentElement.querySelector('.monitor-chart-legend');
        legend.innerHTML = groups.map(group => `<span><i style="background:${group.color}"></i>${escape(group.name)}</span>`).join('');
        if (!points.length) { host.innerHTML = '<div class="monitor-chart-empty"><span>暂无独立入账趋势点</span><p>当前窗口没有可独立绘制的有效计算点；数据可能已合并到汇总记录、处于待核对状态，或尚未采集。请在项目数据页核对。</p></div>'; return; }
        const width = Math.max(host.clientWidth || 800, 280), height = 300;
        const left = width < 450 ? 61 : 76, right = 20, top = 26, bottom = 43;
        const plotWidth = width-left-right, plotHeight = height-top-bottom;
        const rawStart = Math.min(...points.map(p=>p.time)), rawEnd = Math.max(...points.map(p=>p.time));
        const start = rawStart === rawEnd ? rawStart-1800000 : rawStart, end = rawStart === rawEnd ? rawEnd+1800000 : rawEnd;
        const low = Math.min(...points.map(p=>p.value), ...(metric === 'carbon_reduction' ? [0] : [])), high = Math.max(...points.map(p=>p.value), ...(metric === 'carbon_reduction' ? [0] : []));
        const span = high-low || 0.001;
        let min = metric === 'cumulative_reduction' ? low-span*0.12 : (low < 0 ? low-span*0.12 : 0);
        let max = metric === 'cumulative_reduction' ? high+span*0.12 : (high > 0 ? high+span*0.12 : (low < 0 ? 0 : 0.001));
        const roughStep = (max-min)/4;
        const scale = 10 ** Math.floor(Math.log10(roughStep));
        const step = [1, 2, 2.5, 5, 10].find(value => value*scale >= roughStep)*scale;
        min = Math.floor(min/step)*step; max = Math.ceil(max/step)*step;
        const gridCount = Math.round((max-min)/step);
        const x = time => left+(time-start)/(end-start)*plotWidth, y = value => top+(max-value)/(max-min)*plotHeight;
        const axisFormat = value => Number(value.toFixed(6)).toLocaleString('zh-CN', {maximumFractionDigits: 6});
        let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="近24小时${metric === 'carbon_reduction' ? '本期' : '累计'}减排趋势，按机器分线"><title>减排量趋势，单位：吨二氧化碳当量</title><text x="${left}" y="13" class="monitor-axis-unit">减排量（tCO₂e）</text>`;
        for (let i=0;i<=gridCount;i++) {
            const value = max-step*i, yy=y(value);
            svg += `<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" class="monitor-grid-line"/><text x="${left-12}" y="${yy+4}" text-anchor="end" class="monitor-axis-label">${axisFormat(value)}</text>`;
        }
        const ticks = width < 500 ? 2 : 4;
        for (let i=0;i<=ticks;i++) {
            const time=start+(end-start)*i/ticks, date=new Date(time), label=`${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
            svg+=`<text x="${x(time)}" y="${height-15}" text-anchor="${i===0?'start':i===ticks?'end':'middle'}" class="monitor-axis-label">${label}</text>`;
        }
        for (const group of groups) {
            let path='', previous=null;
            for (const point of group.points) {
                if (point.value === null) {previous=null;continue;}
                // 不把机器间、计量阶段间或缺失数值连接为一条连续曲线。
                const connects = previous && point.segment === previous.segment;
                path+=`${connects?'L':'M'}${x(point.time).toFixed(2)},${y(point.value).toFixed(2)} `;
                previous=point;
            }
            svg+=`<path d="${path}" fill="none" stroke="${group.color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
            for (const point of group.points.filter(p=>p.value!==null)) svg+=`<circle cx="${x(point.time)}" cy="${y(point.value)}" r="${points.length < 30 ? 3.5 : 2}" fill="${group.color}"/>`;
        }
        svg+='</svg><div class="monitor-chart-tooltip" hidden></div>';
        host.innerHTML=svg;
        const chart=host.querySelector('svg'), tooltip=host.querySelector('.monitor-chart-tooltip');
        const hide=()=>{tooltip.hidden=true;};
        chart.addEventListener('pointerleave', hide);
        chart.addEventListener('pointermove', event=>{
            const rect=chart.getBoundingClientRect(), px=(event.clientX-rect.left)*width/rect.width, py=(event.clientY-rect.top)*height/rect.height;
            if (px<left || px>width-right || py<top || py>height-bottom) {hide();return;}
            let nearest=null, distance=Infinity;
            for (const group of groups) for (const point of group.points) {
                if (point.value===null) continue;
                const d=(x(point.time)-px)**2+(y(point.value)-py)**2;
                if (d<distance) {distance=d;nearest={point,group};}
            }
            const {point,group}=nearest;
            tooltip.innerHTML=`<strong>${escape(group.name)}</strong><span>${escape(point.timestamp)}</span><b>${format(point.value,6)} <small>tCO₂e</small></b>`;
            tooltip.hidden=false;
            tooltip.style.left=`${Math.max(0,Math.min(event.clientX-rect.left+14,host.clientWidth-tooltip.offsetWidth))}px`;
            tooltip.style.top=`${Math.max(0,event.clientY-rect.top-tooltip.offsetHeight-12)}px`;
        });
    }
    function render(container, latest, history, machines=[], options={}) {
        if (observer) {observer.disconnect();observer=null;}
        if (!latest) {container.innerHTML='<div class="monitor-chart-empty"><span>暂无监测数据</span><p>请先录入数据，或等待设备完成采集。</p></div>';return;}
        const snapshots = options.machineSnapshots || [];
        const projectKey = String(options.projectId ?? 'preview');
        const machineKey = row => row.machine_id == null ? 'unassigned' : String(row.machine_id);
        if (snapshots.length) {
            const saved = selectedMachineByProject.get(projectKey);
            latest = snapshots.find(row => machineKey(row) === saved)
                || snapshots.find(row => machineKey(row) === machineKey(latest)) || snapshots[0];
            selectedMachineByProject.set(projectKey, machineKey(latest));
        }
        const machineSelector = snapshots.length > 1 ? `<label class="monitor-machine-filter">查看机器读数 <select aria-label="查看机器读数" class="input">${snapshots.map(row => `<option value="${machineKey(row)}" ${machineKey(row)===machineKey(latest)?'selected':''}>${escape(machineName(row.machine_id,machines))}</option>`).join('')}</select></label>` : '';
        const name=machineName(latest.machine_id,machines), bd=parse(latest.breakdown), booking=readingStatus(latest);
        container.innerHTML=`${options.projectCumulative != null ? `<div class="monitor-project-total"><div><span>项目全历史累计减排量</span><strong>${format(options.projectCumulative,2)} <small>tCO₂e</small></strong></div><p>项目各机器最新累计之和（包含已作废机器的历史），不随下方机器选择切换；不是待核证量。</p></div>` : ''}
            ${machineSelector}<div class="monitor-context"><div><span class="monitor-status-dot"></span><strong>当前机器的最新记录</strong><span class="monitor-context-machine">${escape(name)}</span></div><span>数据时间：${escape(latest.timestamp)}</span></div>
            <div class="monitor-grid monitor-summary">
                <div class="monitor-card"><div class="metric-label">本期新增入账减排量</div><div class="metric-value">${amountHtml(latest)} ${booking.counted ? '<span class="metric-unit">tCO₂e</span>' : ''}</div><p>${escape(booking.detail || '该机器本条记录对应计量区间的新增入账减排量')}</p></div>
                <div class="monitor-card"><div class="metric-label">该机器全历史累计减排量</div><div class="metric-value">${format(latest.cumulative_reduction,2)} <span class="metric-unit">tCO₂e</span></div><p>历史有效减排之和，不代表项目待核证量</p></div>
            </div>
            <section class="card monitor-trend-card"><div class="monitor-section-heading"><div><h2>减排量趋势</h2><p>近 24 小时 · 最近 ${history.length} 条记录 · 全部机器分线展示</p></div><div class="monitor-chart-switch" role="group" aria-label="选择趋势指标"><button type="button" data-monitor-metric="carbon_reduction">本期减排</button><button type="button" data-monitor-metric="cumulative_reduction">累计减排</button></div></div><div class="monitor-chart-legend"></div><div class="monitor-trend-plot"></div><p class="monitor-chart-note">时间轴按平台记录时间排列；日汇总的 00:00 标签可能包含当日后续读数，不能当作实际采样时点。已合并、待核对及计量起点不画作零减排点；未补零、未平滑。每条记录覆盖时长可能不同，本期减排量不等同于瞬时减排速率。最多展示最近 200 条记录。</p></section>
            <section class="card monitor-calculation"><div class="monitor-section-heading"><div><h2>本条记录怎么算</h2><p>对应上方机器及数据时间，单位随计算项显示。</p></div><span class="monitor-section-badge">计算分解</span></div>${breakdownHtml(bd)}</section>`;
        const selector = container.querySelector('.monitor-machine-filter select');
        if (selector) selector.addEventListener('change', () => {
            selectedMachineByProject.set(projectKey, selector.value);
            render(container, latest, history, machines, options);
        });
        const host=container.querySelector('.monitor-trend-plot');
        const draw=()=>renderChart(host,history,machines);
        const switches=container.querySelectorAll('[data-monitor-metric]');
        const sync=()=>switches.forEach(button=>{const active=button.dataset.monitorMetric===metric;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
        switches.forEach(button=>button.addEventListener('click',()=>{metric=button.dataset.monitorMetric;sync();draw();}));
        sync();draw();
        if (typeof ResizeObserver!=='undefined') {let previousWidth=host.clientWidth;observer=new ResizeObserver(()=>{if(host.clientWidth>0&&host.clientWidth!==previousWidth){previousWidth=host.clientWidth;draw();}});observer.observe(host);}
    }
    return {render, prepareSeries, breakdownHtml, readingStatus, amountHtml};
})();
