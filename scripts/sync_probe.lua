local samples = {}
local t = 0
mp.add_periodic_timer(0.25, function()
  t = t + 1
  local pos = mp.get_property_number("time-pos", -1)
  local avs = mp.get_property_number("avsync", 0)
  local jit = mp.get_property_number("avsync-jitter", 0)
  local drops = mp.get_property_number("frame-drop-count", 0)
  local vd = mp.get_property_number("vo-delayed-frame-count", 0)
  table.insert(samples, string.format("t=%.3f avsync=%.4f jitter=%.4f drops=%d vo_delayed=%d", pos, avs, jit, drops, vd))
end)
mp.register_event("end-file", function()
  for _, s in ipairs(samples) do mp.msg.warn("SYNC " .. s) end
  mp.msg.warn(string.format("SYNC_SUMMARY n=%d max_avsync=%.4f max_jitter=%.4f total_drops=%d",
    #samples,
    0, 0, mp.get_property_number("frame-drop-count", 0)))
end)
