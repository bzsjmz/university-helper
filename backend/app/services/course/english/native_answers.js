// Read only explicitly bound courseware solutions. No global answer guessing.
q => {
  const norm = value => String(value ?? '').replace(/\s+/g, ' ').trim();
  const controls = q.controls.map(id => document.querySelector(`[data-uh-control="${id}"]`));
  if (controls.some(e => !e)) return { candidates: [], binding: {} };
  const root = controls[0].closest('[data-uh-question-root]');
  if (!root || controls.some(e => e.closest('[data-uh-question-root]') !== root))
    return { candidates: [], binding: {} };
  const attrs = e => ['id','name','identifier','data-identifier','data-question-id',
    'data-response-identifier','responseidentifier','response-identifier']
    .map(key => e.getAttribute(key)).filter(v => v && v.length <= 256);
  const optionKeys = controls.map(e => [...new Set([...attrs(e),
    ...(e.hasAttribute('value') ? [e.getAttribute('value')] : [])])]);
  if (q.values.length) {
    const offered = [...controls[0].options].filter(o => !o.disabled && o.value !== '');
    optionKeys.splice(0, optionKeys.length, ...offered.map(o => [...new Set([...attrs(o), o.value])]));
  }
  const binding = { root_keys: attrs(root), control_keys: controls.map(attrs), option_keys: optionKeys };
  const parent = root.closest('[data-assessment-item-id]');
  if (parent) binding.root_keys.push(parent.getAttribute('data-assessment-item-id'));
  const decode = raw => {
    if (typeof raw !== 'string' || raw.length > 10000) return null;
    let value = raw.trim();
    if (/^[\[{"]/.test(value)) {
      try { value = JSON.parse(value); } catch (_) { return null; }
      if (Array.isArray(value) && value.length === 1) value = value[0];
    }
    if (typeof value !== 'string') return null;
    value = norm(value);
    if (!value || /[<>|/;]/.test(value) || (q.type === 'completion' && /[,，]/.test(value)) ||
      /^(?:\(?open\.?\)?|\(?answers? (?:may|will) vary\.?\)?|无标准答案|略)$/i.test(value)) return null;
    return value;
  };
  const candidates = []; let uncertain = false;
  const add = answer => {
    if (!answer || candidates.length >= 20) return;
    if (q.type === 'single' || q.type === 'multiple') {
      if (!answer.length || (q.type === 'single' && answer.length !== 1)) return;
      answer = [...answer].sort((a,b) => a-b);
      if (new Set(answer).size !== answer.length) return;
    } else if (answer.length !== q.count || answer.some(s => !s)) return;
    candidates.push(answer);
  };
  const matchOption = token => {
    const matches = q.options.map((text,i) => norm(text) === token || optionKeys[i]?.includes(token) ? i : -1).filter(i => i >= 0);
    return matches.length === 1 ? matches[0] : null;
  };
  const nodes = [root, ...root.querySelectorAll('[data-solution]')]
    .filter(e => e.hasAttribute('data-solution') && e.closest('[data-uh-question-root]') === root);
  if (q.type === 'single' || q.type === 'multiple') {
    const marked = [];
    for (const node of nodes) {
      const raw = node.getAttribute('data-solution');
      if (raw === '' || raw === 'true' || (controls.includes(node) && node.tagName === 'LI' &&
          root.matches('[data-controltype="choice"]') && !['false','0'].includes(raw))) {
        const owned = controls.map((e,i) => e === node || node.contains(e) ||
          [...(e.labels || [])].some(label => label === node || label.contains(node)) ? i : -1).filter(i => i >= 0);
        if (owned.length === 1) marked.push(owned[0]);
      } else if (raw !== 'false') {
        let tokens;
        try { tokens = JSON.parse(raw); } catch (_) { tokens = raw; }
        if (!Array.isArray(tokens)) tokens = [tokens];
        if (!tokens.length || tokens.some(t => typeof t !== 'string')) { uncertain = true; continue; }
        const indexes = tokens.map(decode).map(t => t === null ? null : matchOption(t));
        if (indexes.every(i => i !== null)) add(indexes);
        else uncertain = true;
      }
    }
    if (marked.length) {
      const unique = [...new Set(marked)];
      if (q.type === 'single' && unique.length !== 1) uncertain = true;
      add(unique);
    }
  } else {
    const answers = controls.map(e => decode(e.getAttribute('data-solution')));
    // WE courseware binds a hidden result node to one filling widget. Never
    // treat an unrelated page-wide result or a previous input value as an answer.
    if (q.count === 1 && root.matches('et-blank')) {
      const keys = [...root.querySelectorAll('.key')].filter(e => e.closest('et-blank') === root);
      if (keys.length === 1) add([decode(keys[0].textContent)]);
    }
    if (q.count === 1 && root.matches('[data-controltype="filling"], [data-controltype="fillinglong"]')) {
      const results = [...root.querySelectorAll('[data-itemtype="result"]')]
        .filter(e => e.closest('[data-controltype]') === root);
      if (results.length === 1) add([decode(results[0].textContent)]);
    }
    if (answers.every(v => v !== null)) add(answers);
    // A question-level scalar can identify one blank, never an ordered group.
    if (q.count === 1) for (const node of nodes) {
      const answer = decode(node.getAttribute('data-solution') ||
        (node === root || controls.includes(node) ? '' : node.textContent));
      if (answer !== null) add([answer]);
    }
  }
  return { candidates: uncertain ? [] : candidates, binding };
}
