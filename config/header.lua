-- The header's right side as a hierarchy: what you work on first and strongest, the rest quiet.
-- Wire: local hd = require('header')(config, wezterm, as)   (as = agentstatus)
--   hd.context(window)  -> the lead chip's model { workspace, project, branch, counts = { working, busy, attention, limit } }, or nil
--   hd.tiers(model)     -> the chip richest first as { items, w }: project (and workspace) in
--                          bright bold, the git branch of the active pane, and the agents that
--                          run or want you, in the state vocabulary (theme.STATES): glyph in the
--                          state's colour, count, label. Sits on the one pill the header has;
--                          the agent part is the click target `agents`.
--   hd.git(dir)         -> branch, repo root of a directory (a .git/HEAD read, cached a few seconds)
--   hd.fit(groups, lead_w, avail) -> boolean: runs PLAN over the groups { A, B, C } (each
--                          { tiers }) and sets .tier on each; true when it fits without a drop
-- Quota (B) and system stats (C) are muted text by default and take a colour only past a warning
-- threshold (see claudequota.lua, sysstats.lua), so only green, amber and red carry meaning.
return function(_config, wezterm, as)
  local theme = require 'theme'
  local P = theme.P
  local api = {}

  -- How the right status gives up width: { 'tier', group, tier } takes a group to a narrower
  -- tier (one that is not narrower than the current one is skipped), { 'drop', group } removes
  -- it. Metadata narrows first: the quota bars, the system amounts, then the context's workspace
  -- and branch; the quota group goes before the context loses its last words. The context's
  -- last tier (a short project name and the agent counts), the CPU chip alone and the mode and
  -- zoom pills are never dropped; the pills' key hints go before any group narrows.
  api.PLAN = {
    { 'tier', 'B', 2 }, { 'tier', 'C', 2 }, { 'tier', 'A', 2 }, { 'tier', 'B', 3 },
    { 'tier', 'B', 4 }, { 'tier', 'A', 3 }, { 'tier', 'C', 3 }, { 'tier', 'B', 5 },
    { 'drop', 'B' }, { 'tier', 'A', 4 },
  }
  -- Cells between two groups of the right status.
  api.GAP = 2

  function api.fit(groups, lead_w, avail)
    local function needed()
      local w = lead_w
      for _, g in pairs(groups) do
        local t = g.tiers[g.tier]
        if t then w = w + t.w + api.GAP end
      end
      return w
    end
    for _, g in pairs(groups) do g.tier = 1 end
    local dropped = false
    for _, step in ipairs(api.PLAN) do
      if needed() <= avail then break end
      local g = groups[step[2]]
      if g and step[1] == 'tier' then
        local cur, to = g.tiers[g.tier], g.tiers[step[3]]
        if to and (not cur or to.w < cur.w) then g.tier = step[3] end
      elseif g and step[1] == 'drop' then
        g.tier, dropped = math.huge, true
      end
    end
    return not dropped and needed() <= avail
  end

  -- Git ---------------------------------------------------------------------------------

  local function slurp(path)
    local f = io.open(path, 'r')
    if not f then return nil end
    local ok, text = pcall(f.read, f, 'a')
    f:close()
    return ok and text or nil
  end

  -- The directory holding .git above `dir` and the text of its HEAD. A worktree or submodule
  -- has a .git file naming the real git directory.
  local function find_head(dir)
    local d = dir
    for _ = 1, 64 do
      local head = slurp(d .. '/.git/HEAD')
      if not head then
        local redirect = (slurp(d .. '/.git') or ''):match('^gitdir:%s*(.-)%s*\n?$')
        if redirect and redirect ~= '' then
          if redirect:sub(1, 1) ~= '/' then redirect = d .. '/' .. redirect end
          head = slurp(redirect .. '/HEAD')
        end
      end
      if head then return d, head end
      local parent = d:match('^(.*)/[^/]+$')
      if not parent or parent == d then return nil end
      d = parent == '' and '/' or parent
    end
  end

  local GIT_TTL = 5
  local memo, memo_n = {}, 0

  -- Branch name (a detached HEAD reads as its short hash) and the repository root of `dir`;
  -- nil, nil outside a repository.
  function api.git(dir)
    if not dir or dir == '' then return nil, nil end
    local now = os.time()
    local hit = memo[dir]
    if hit and now - hit.at < GIT_TTL then return hit.branch, hit.root end
    local root, head = find_head(dir)
    local branch
    if head then
      branch = head:match('^ref:%s*refs/heads/(.-)%s*$') or head:match('^ref:%s*(.-)%s*$')
      branch = branch or head:match('^(%x%x%x%x%x%x%x)')
    end
    if memo[dir] == nil then
      if memo_n >= 32 then memo, memo_n = {}, 0 end -- directories come and go: stay small
      memo_n = memo_n + 1
    end
    memo[dir] = { at = now, branch = branch, root = root }
    return branch, root
  end

  -- Model ---------------------------------------------------------------------------------

  local function basename(path)
    return path:match('([^/]+)/*$') or path
  end

  -- What the lead chip says now. The project is the repository (else the directory) of the
  -- active grid pane; a workspace other than `default` goes before it.
  function api.context(window)
    local dir = as.focus_dir(window)
    local branch, root = api.git(dir)
    local project
    if dir then
      local home = wezterm.home_dir
      if root then
        project = basename(root)
      elseif home and dir:gsub('/+$', '') == home:gsub('/+$', '') then
        project = '~'
      else
        project = basename(dir)
      end
    end
    local ok, workspace = pcall(window.active_workspace, window)
    if not ok or workspace == 'default' or workspace == '' then workspace = nil end
    local counts = as.running(window)
    local any = counts.attention + (counts.limit or 0) + counts.working + counts.busy > 0
    if not (project or workspace or any) then return nil end
    return { workspace = workspace, project = project, branch = branch, counts = counts }
  end

  -- Tiers ---------------------------------------------------------------------------------

  -- Names come from the pane's directory (OSC 7) and the .git/HEAD file: control bytes never
  -- reach the header string.
  local function cut(s, n)
    s = s:gsub('%c', '')
    local len = utf8.len(s) or #s
    if len <= n then return s end
    return s:sub(1, (utf8.offset(s, n) or n + 1) - 1) .. '…'
  end

  -- Most urgent first: needs you, then a usage limit, then working, then busy.
  local KINDS = { 'attention', 'limit', 'working', 'busy' }

  local function agents_items(counts, verbose)
    local items = {}
    for _, kind in ipairs(KINDS) do
      local n = counts[kind] or 0
      if n > 0 then
        local s = theme.STATES[kind]
        if #items > 0 then items[#items + 1] = { Text = verbose and '  ' or ' ' } end
        theme.extend(items, {
          { Foreground = { Color = s.color } }, { Text = s.glyph },
          { Foreground = { Color = P.text } },
          { Text = verbose and (' ' .. n .. ' ' .. s.text) or tostring(n) },
        })
      end
    end
    return items
  end

  -- opts: ws (workspace chars kept, 0 none), name, branch (chars kept, 0 none), verbose.
  local function build(m, opts)
    local body = {}
    if m.workspace and opts.ws > 0 then
      theme.extend(body, {
        { Foreground = { Color = P.muted } }, { Text = cut(m.workspace, opts.ws) .. ' · ' },
      })
    end
    if m.project then
      theme.extend(body, {
        { Foreground = { Color = P.bright } }, { Attribute = { Intensity = 'Bold' } },
        { Text = cut(m.project, opts.name) }, { Attribute = { Intensity = 'Normal' } },
      })
    end
    if m.branch and opts.branch > 0 then
      theme.extend(body, {
        { Foreground = { Color = P.dim } }, { Text = ' · ' },
        { Foreground = { Color = P.text } }, { Text = cut(m.branch, opts.branch) },
      })
    end
    local agents = agents_items(m.counts, opts.verbose)
    if #agents > 0 then
      if #body > 0 then body[#body + 1] = { Text = opts.verbose and '   ' or '  ' } end
      theme.extend(body, theme.click('agents', agents))
    end
    if #body == 0 then return { items = {}, w = 0 } end
    local items = theme.pill(body, P.pill)
    return { items = items, w = theme.width(items) }
  end

  function api.tiers(m)
    if not m then return {} end
    local out = {}
    for _, opts in ipairs {
      { ws = 16, name = 28, branch = 24, verbose = true },
      { ws = 0, name = 24, branch = 16 },
      { ws = 0, name = 16, branch = 0 },
      { ws = 0, name = 10, branch = 0 },
    } do
      out[#out + 1] = build(m, opts)
    end
    return out
  end

  return api
end
