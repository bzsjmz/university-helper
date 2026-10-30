() => {
  const visible = e => !!e.getClientRects().length && !['hidden','collapse'].includes(getComputedStyle(e).visibility) && !e.closest('[hidden], [aria-hidden="true"]');
  const text = e => {
    if (!e) return '';
    const copy = e.cloneNode(true);
    copy.querySelectorAll('[data-itemtype="result"], et-blank .key, et-blank .blank, et-blank textarea, et-tof .controls, script, style, [hidden], [aria-hidden="true"]').forEach(n => n.remove());
    return (copy.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const body = text(document.body);
  if (/(请输入验证码|滑动验证|人脸识别|人脸验证|监考|考试口令|已截止|已过期|请先登录)/.test(body))
    return { questions: [], unsupported: 1, blocked: true };
  const context = Array.from(document.querySelectorAll('.passage, .reading-passage, .article-content, .instruction, .transcript, et-script'))
    .filter(e => visible(e) || e.matches('et-script')).map(text).join('\n').slice(0,10000);
  const matching = e => e.matches('et-blank span.blank[tabindex]:not([contenteditable])') && !!e.closest('[noinput]');
  const controlType = e => matching(e) ? 'welearn-match' : e.matches('et-blank .blank[contenteditable]:not([contenteditable="false"])') ? 'contenteditable' : e.type;
  const all = Array.from(document.querySelectorAll('input, textarea, select, et-blank .blank[contenteditable]:not([contenteditable="false"]), et-blank span.blank[tabindex]:not([contenteditable])')).filter(e => visible(e) && !e.closest('nav, header, [role=search], et-toggle'));
  const editable = all.filter(e => !['hidden','button','submit','reset'].includes(controlType(e)) && !e.disabled && !e.readOnly);
  document.querySelectorAll('[data-uh-control]').forEach(e => e.removeAttribute('data-uh-control'));
  editable.forEach((e,i) => e.setAttribute('data-uh-control', String(i)));
  const roots = new Map(); let unsupported = 0;
  document.querySelectorAll('[data-uh-question-root]').forEach(e => e.removeAttribute('data-uh-question-root'));
  for (const e of editable) {
    const root = e.closest('et-choice, et-tof, et-blank, .question, .question-item, [data-question-id], [class*="questionDefault"], [data-controltype="filling"], [data-controltype="fillinglong"], [data-controltype="choice"]');
    if (!root || !['radio','checkbox','text','search','textarea','select-one','contenteditable','welearn-match'].includes(controlType(e))) {
      unsupported++; continue;
    }
    if (!roots.has(root)) roots.set(root, []);
    roots.get(root).push(e);
  }
  unsupported += Array.from(document.querySelectorAll('[contenteditable]:not([contenteditable="false"])')).filter(e => visible(e) && !e.closest('et-toggle') && !editable.includes(e)).length;
  // A custom courseware widget with no supported native controls must not be
  // silently ignored when another question on the same page is recognized.
  unsupported += [...document.querySelectorAll('et-choice, et-tof, et-blank, et-matching, et-sort, [data-controltype="filling"], [data-controltype="fillinglong"]')]
    .filter(e => visible(e) && !(e.matches('et-choice') && (e.querySelector('ol > li') || e.matches('[circle]') && e.querySelector(':scope > .wrapper > span[ng-click]'))) &&
      !(e.matches('et-tof') && e.querySelectorAll(':scope > .wrapper > .controls > span').length === 2) &&
      ![...e.querySelectorAll('input:not([type="hidden"]):not([type="button"]):not([type="submit"]), textarea, select, .blank[contenteditable]:not([contenteditable="false"]), span.blank[tabindex]')].some(c => visible(c) && (c.hasAttribute('contenteditable') || matching(c) || /^(INPUT|TEXTAREA|SELECT)$/.test(c.tagName)))).length;
  const questions = [];
  for (const [root, controls] of roots) {
    const courseware = root.matches('et-blank, [data-controltype="filling"], [data-controltype="fillinglong"]');
    const line = courseware ? (root.parentElement?.closest('p, tr, li') || root.parentElement) : null;
    const ownStem = text(root.querySelector('.question-title, .stem, [class*="questionTitle"]')) || text(root);
    let localStem = courseware && !/[A-Za-z\u4e00-\u9fff]/.test(ownStem) ? text(line) : ownStem;
    if (!localStem && root.matches('et-blank[notscored]')) localStem = text(root.closest('tab') || root.parentElement);
    const nativeOnlyBlank = courseware && !localStem;
    const stem = nativeOnlyBlank ? `Fill blank ${questions.length + 1}` : localStem;
    const nativeOnly = nativeOnlyBlank || courseware && !root.hasAttribute('block') && !root.hasAttribute('notscored') && !!(document.getElementsByTagName('audio').length || document.getElementsByTagName('video').length) && ![...document.querySelectorAll('.transcript, [class*="transcript"]')].some(e => visible(e) && text(e));
    if (!stem || stem.length > 12000 || [...root.querySelectorAll('img')].some(e => visible(e) && !e.closest('[data-itemtype="result"]')) || (!courseware && root.querySelector('audio,video') && !root.querySelector('.transcript, [class*=transcript]'))) {
      unsupported += controls.length; continue;
    }
    const types = new Set(controls.map(controlType));
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
    } else if (Array.from(types).every(t => ['text','search','textarea','contenteditable','welearn-match'].includes(t))) {
      type = types.has('textarea') ? 'shortanswer' : 'completion';
    } else { unsupported += controls.length; continue; }
    root.setAttribute('data-uh-question-root', String(questions.length));
    questions.push({ type, title: stem, context, options,
      count: ['single','multiple'].includes(type) ? 1 : controls.length,
      ...(nativeOnly ? {requires_native: true} : {}), controls: ids, control_types: controls.map(controlType), values });
  }
  // WE Learn also uses clickable option rows rather than radio inputs.
  for (const root of document.querySelectorAll('[data-controltype="choice"], et-choice')) {
    if (!visible(root) || roots.has(root)) continue;
    const etChoice = root.matches('et-choice');
    const circle = etChoice && root.hasAttribute('circle');
    const rows = [...root.querySelectorAll(circle ? ':scope > .wrapper > span[ng-click]' : etChoice ? ':scope > .wrapper > ol > li' : 'ul[data-itemtype="options"] > li')].filter(visible);
    if (circle && rows.some((e,i) => e.getAttribute('ng-click') !== `choice.select(${i})`)) { unsupported++; continue; }
    const title = circle ? text(root.parentElement?.closest('p,li,tr') || root.parentElement) :
      text(root.querySelector(etChoice ? ':scope > .wrapper > span' : '.question-title, .stem, [data-itemtype="question"]')) || text(root);
    const options = rows.map(text);
    if (!title || rows.length < 2 || rows.length > 26 || new Set(options).size !== rows.length || options.some(s => !s) || root.querySelector('img, audio, video')) {
      unsupported++; continue;
    }
    const ids = rows.map((e,i) => {
      const id = String(editable.length + questions.length * 30 + i);
      e.setAttribute('data-uh-control', id); return id;
    });
    root.setAttribute('data-uh-question-root', String(questions.length));
    questions.push({type:'single', title, context, options, count:1, controls:ids,
      control_types:rows.map(() => 'welearn-option'), values:[]});
  }
  // ET true/false widgets expose two native clickable spans, not radio inputs.
  for (const root of document.querySelectorAll('et-tof')) {
    if (!visible(root) || roots.has(root)) continue;
    const rows = [...root.querySelectorAll(':scope > .wrapper > .controls > span')].filter(visible);
    const options = rows.map(text);
    const nativeValues = rows.map(e => e.getAttribute('ng-click')?.match(/^tof\.chose\(['"]([tf])['"]\)$/)?.[1]);
    const title = text(root.querySelector(':scope > .wrapper > [ng-transclude]')) || text(root);
    if (rows.length !== 2 || !title || options.some(s => !s) || new Set(options).size !== 2 ||
        nativeValues.some(v => !v) || new Set(nativeValues).size !== 2) {
      unsupported++; continue;
    }
    const ids = rows.map((e,i) => {
      const id = String(editable.length + questions.length * 30 + i);
      e.setAttribute('data-uh-control', id); return id;
    });
    root.setAttribute('data-uh-question-root', String(questions.length));
    questions.push({type:'single', title, context, options, count:1, controls:ids,
      control_types:rows.map(() => 'welearn-option'), values:[], native_values:nativeValues});
  }
  if (questions.length > 100) return { questions: [], unsupported: editable.length, blocked: true };
  const pendingRecordings = [...document.querySelectorAll('et-recorder,et-follow-me,et-talk')].filter(e => {
    if (!visible(e) || e.parentElement?.closest('et-talk,et-follow-me,et-recorder') || e.closest('[noprogress]')) return false;
    // Existing recordings restored by the platform need no new recording. This
    // is a read-only check; the native submit still validates the whole lesson.
    if (e.matches('et-recorder')) try {
      const recorder = window.angular?.element(e).controller('etRecorder');
      if (Number.isFinite(recorder?.recordingCount) && recorder.recordingCount > 0 &&
          recorder.isRecording === false && recorder.reset === false) return false;
    } catch (_) {}
    if (!e.matches('et-recorder')) try {
      const name = e.matches('et-follow-me') ? 'etFollowMe' : 'etTalk';
      const control = window.angular?.element(e).controller(name);
      if (Number.isInteger(control?.recordingCount) && control.recordingCount > 0 &&
          control.isRecording !== true && control.isTalking !== true && control.reset !== true &&
          (name === 'etTalk' || control.isCompleted?.() === true)) return false;
    } catch (_) {}
    return true;
  });
  const recording_details = pendingRecordings.map(e => {
    let recorder;
    try { recorder = window.angular?.element(e).controller('etRecorder'); } catch (_) {}
    const script = typeof recorder?.script === 'string' ? recorder.script : (e.getAttribute('script') || '');
    return {id: e.id || '', kind: e.matches('et-recorder') ? 'recording' : 'conversation',
      scored: e.matches('et-follow-me') || !!script.trim() || recorder?.scriptFlag === true,
      script: script.slice(0,10000), app_required: recorder?.isMobile === false};
  });
  return { questions, unsupported, blocked: false, recordings: pendingRecordings.length, recording_details };
}
