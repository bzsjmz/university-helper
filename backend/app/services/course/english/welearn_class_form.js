() => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[hidden],[aria-hidden="true"]');
  const text = e => {
    const copy = e?.cloneNode(true);
    if (!copy) return '';
    copy.querySelectorAll('script,style,input,select,textarea,[class*="answer"],.test_number').forEach(n => n.remove());
    return (copy.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const body = text(document.body);
  const blocked = /验证码|人脸|监考|考试口令|未开始|尚未开放|已截止|已过期|请先登录/.test(body);
  const blocks = [...document.querySelectorAll('.itemDiv')].filter(visible);
  const questions = []; let unsupported = 0;
  document.querySelectorAll('[data-uh-control]').forEach(e => e.removeAttribute('data-uh-control'));
  document.querySelectorAll('[data-uh-question-root]').forEach(e => e.removeAttribute('data-uh-question-root'));
  const editable = [...document.querySelectorAll('input,select,textarea,[contenteditable="true"]')].filter(e => visible(e) && !['hidden','button','submit','reset'].includes(e.type));
  const used = new Set(); const roots = new Set(); let nextId = 0;
  for (const block of blocks) {
    // Audio-only tests cannot be answered by a text model without a transcript.
    if (block.querySelector('img') || block.querySelector('audio,video,a[href*="PlaySound"],[data-soundsrc]') && !block.querySelector('.transcript')) { unsupported++; continue; }
    const context = text(block.querySelector('.col-md-8,.passage,.reading-passage,.transcript'));
    const groups = new Map();
    for (const input of editable.filter(e => block.contains(e))) {
      if (input.disabled || input.readOnly || !/^(radio|checkbox|text|textarea|select-one)$/.test(input.type)) { unsupported++; continue; }
      const key = ['radio','checkbox'].includes(input.type) ? input.name : input.id;
      if (!key || input.matches('[contenteditable]')) { unsupported++; continue; }
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(input);
    }
    for (const controls of groups.values()) {
      const root = controls[0].closest('.test_hov,.question,.form-inline');
      if (!root || !block.contains(root) || roots.has(root) || controls.some(e => !root.contains(e)) || root.querySelectorAll('[id^="question_"]').length > 1) { unsupported++; continue; }
      roots.add(root);
      const title = text(root.querySelector('.stem,.question-title')) || text(root.querySelector(':scope > div')) || text(root);
      let type, options = [], values = [];
      if (!title || title.length > 12000 || context.length > 10000) { unsupported++; continue; }
      if (controls.every(e => e.type === 'radio') || controls.every(e => e.type === 'checkbox')) {
        type = controls[0].type === 'radio' ? 'single' : 'multiple';
        options = controls.map(e => text(e.labels?.[0] || e.closest('label')).replace(/^[A-Z][).、．]\s*/, ''));
        if (options.length < 2 || options.length > 26 || options.some(s => !s) || new Set(options).size !== options.length) { unsupported++; continue; }
      } else if (controls.length === 1 && controls[0].type === 'select-one') {
        type = 'single';
        const offered = [...controls[0].options].filter(o => !o.disabled && o.value !== '');
        options = offered.map(text); values = offered.map(o => o.value);
        if (options.length < 2 || options.length > 26 || options.some(s => !s) || new Set(options).size !== options.length) { unsupported++; continue; }
      } else if (controls.length === 1 && ['text','textarea'].includes(controls[0].type)) {
        type = controls[0].type === 'text' ? 'completion' : 'shortanswer';
      } else { unsupported++; continue; }
      const ids = controls.map(e => { const id = String(nextId++); e.setAttribute('data-uh-control', id); used.add(e); return id; });
      root.setAttribute('data-uh-question-root', String(questions.length));
      questions.push({type, title, context, count: 1, options, values, controls: ids, control_types: controls.map(e => e.type)});
    }
    if (!groups.size) unsupported++;
  }
  unsupported += editable.filter(e => !used.has(e)).length;
  // A question marker with no mapped controls must not silently disappear.
  unsupported += [...document.querySelectorAll('[id^="question_"]')].filter(visible).filter(e => !e.closest('[data-uh-question-root]') && !e.parentElement?.querySelector('[data-uh-question-root]')).length;
  return {questions, unsupported, blocked};
}
