({apply = false} = {}) => {
  // Explicit experiment: these are completion records, not uploaded audio.
  // Preflight the entire visible recording page before changing any state.
  const visible = e => !!e?.getClientRects().length &&
    !['hidden', 'collapse'].includes(getComputedStyle(e).visibility) &&
    !e.closest('[hidden],[aria-hidden="true"]');
  const elements = [...document.querySelectorAll('et-recorder,et-follow-me,et-talk')]
    .filter(e => visible(e) && !e.parentElement?.closest('et-recorder,et-follow-me,et-talk') &&
      !e.closest('[noprogress]'));
  if (!elements.length) return {ok:true, count:0};
  try {
    const angular = window.angular;
    const plans = [], seen = new Set();
    for (const el of elements) {
      const host = el.closest('et-item');
      const item = angular.element(host).controller('etItem');
      if (!host || !item || item.isBrowseMode || item.isSubmitted ||
          typeof item.handleStatusChange !== 'function' ||
          !Array.isArray(item.interactionIds) || !el.id ||
          !item.interactionIds.includes(el.id) || seen.has(el.id)) return {ok:false};
      seen.add(el.id);
      const name = el.matches('et-recorder') ? 'etRecorder' :
        el.matches('et-follow-me') ? 'etFollowMe' : 'etTalk';
      const control = angular.element(el).controller(name);
      if (!control || !Number.isInteger(control.recordingCount) || control.recordingCount < 0 ||
          control.isRecording === true || control.isTalking === true) return {ok:false};
      // Never overwrite an existing recording or a deliberate reset attempt.
      if (control.recordingCount > 0) {
        if (control.reset === true) return {ok:false};
        continue;
      }
      const filename = index => `uh-experiment-${el.id.replace(/[^A-Za-z0-9_-]/g,'_')}-${index}.mp3`;
      const interaction = {id:el.id, type:'performance', record_count:1};
      if (name === 'etRecorder') {
        if (control.isRecording !== false || control.reset !== false) return {ok:false};
        const scored = control.scriptFlag === true || !!String(control.script || '').trim();
        interaction.result = scored ? '0' : 'neutral';
        interaction.learner_response = filename(0);
      } else if (name === 'etFollowMe') {
        const count = control.sentenceCount;
        if (control.isRecording !== false || !Number.isInteger(count) || count < 1 || count > 100 ||
            el.querySelectorAll('.sentence').length !== count) return {ok:false};
        interaction.result = JSON.stringify(Array.from({length:count}, () => [0,0]));
        interaction.learner_response = Array.from({length:count}, (_,i) => filename(i)).join('[,]');
      } else {
        const roles = control.rolesArray, steps = control.flowSteps;
        if (control.isTalking !== false || !Array.isArray(roles) || roles.length < 1 || roles.length > 20 ||
            !Array.isArray(steps) || steps.length < 1 || steps.length > 100) return {ok:false};
        const recorded = steps.filter(step => step.recorder === true);
        if (!recorded.length || recorded.some(step => !Number.isInteger(step.role) ||
            step.role < 0 || step.role >= roles.length || step.filename)) return {ok:false};
        const chosen = recorded.some(step => step.role === control.chosenRole) ?
          control.chosenRole : recorded[0].role;
        interaction.result = JSON.stringify({
          attempts:roles.map((_,i) => i === chosen ? 1 : 0),
          allScores:recorded.map(() => 0), htmls:steps.map(() => '')
        });
        interaction.learner_response = [`ROLE_${chosen}`, ...recorded.map((_,i) => filename(i))].join('[,]');
      }
      // Use the public controller's dirty queue. Do not write global scores,
      // call the App recording bridge, or send a separate completion request.
      plans.push({item, interaction});
    }
    if (apply && plans.length) {
      const host = elements[0].closest('et-item');
      const root = angular.element(host).injector()?.get('$rootScope');
      if (!root || typeof root.$apply !== 'function') return {ok:false};
      const change = () => plans.forEach(({item, interaction}) => item.handleStatusChange({
        id:interaction.id, isCompleted:true, isScored:false, noProgress:false,
        isDirty:true, interaction
      }));
      if (root.$$phase) change(); else root.$apply(change);
    }
    return {ok:true, count:plans.length};
  } catch (_) {
    return {ok:false};
  }
}
