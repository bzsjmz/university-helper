() => {
  const visible = e => !!e.getClientRects().length && !e.closest('[hidden], [aria-hidden="true"]');
  const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g, ' ').trim();
  const body = text(document.body);
  if (/(请输入验证码|滑动验证|人脸识别|人脸验证|监考|考试口令|已截止|已过期|请先登录)/.test(body))
    return { questions: [], unsupported: 1, blocked: true };
  const context = Array.from(document.querySelectorAll('.passage, .reading-passage, .article-content, .instruction, .transcript'))
    .filter(visible).map(text).join('\n').slice(0,10000);
  const all = Array.from(document.querySelectorAll('input, textarea, select')).filter(e => visible(e) && !e.closest('nav, header, [role=search]'));
  const editable = all.filter(e => !['hidden','button','submit','reset'].includes(e.type) && !e.disabled && !e.readOnly);
  editable.forEach((e,i) => e.setAttribute('data-uh-control', String(i)));
  const roots = new Map(); let unsupported = 0;
  for (const e of editable) {
    const root = e.closest('et-choice, et-tof, et-blank, .question, .question-item, [data-question-id], [class*="questionDefault"]');
    if (!root || !['radio','checkbox','text','search','textarea','select-one'].includes(e.type)) {
      unsupported++; continue;
    }
    if (!roots.has(root)) roots.set(root, []);
    roots.get(root).push(e);
  }
  unsupported += Array.from(document.querySelectorAll('[contenteditable="true"]')).filter(visible).length;
  const questions = [];
  for (const [root, controls] of roots) {
    const stem = text(root.querySelector('.question-title, .stem, [class*="questionTitle"]')) || text(root);
    if (!stem || stem.length > 12000 || root.querySelector('img') || (root.querySelector('audio,video') && !root.querySelector('.transcript, [class*=transcript]'))) {
      unsupported += controls.length; continue;
    }
    const types = new Set(controls.map(e => e.type));
    const ids = controls.map(e => e.getAttribute('data-uh-control'));
    let type, options = [], values = [];
    if (types.size === 1 && (types.has('radio') || types.has('checkbox'))) {
      if (new Set(controls.map(e => e.name)).size !== 1) { unsupported += controls.length; continue; }
      type = types.has('radio') ? 'single' : 'multiple';
      options = controls.map(e => {
        const label = e.labels?.[0] || e.closest('label') || e.parentElement;
        return text(label).replace(/^[A-Z][.、．]\s*/, '');
      });
      if (options.length < 2 || options.length > 26 || options.some(s => !s) || new Set(options).size !== options.length) {
        unsupported += controls.length; continue;
      }
    } else if (types.size === 1 && types.has('select-one') && controls.length === 1) {
      type = 'single';
      const offered = Array.from(controls[0].options).filter(o => !o.disabled && o.value !== '');
      options = offered.map(text); values = offered.map(o => o.value);
      if (options.length < 2 || options.length > 26 || new Set(options).size !== options.length) {
        unsupported++; continue;
      }
    } else if (Array.from(types).every(t => ['text','search','textarea'].includes(t))) {
      type = types.has('textarea') ? 'shortanswer' : 'completion';
    } else { unsupported += controls.length; continue; }
    questions.push({ type, title: stem, context, options,
      count: ['single','multiple'].includes(type) ? 1 : controls.length,
      controls: ids, control_types: controls.map(e => e.type), values });
  }
  if (questions.length > 100) return { questions: [], unsupported: editable.length, blocked: true };
  return { questions, unsupported, blocked: false };
}
