local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local Ponte = require 'Ponte'

LrTasks.startAsyncTask(function()
	local ok, resultado = LrTasks.pcall(Ponte.gravarPresets)
	if ok then
		LrDialogs.showBezel('Álbum: ' .. tostring(resultado) .. ' presets enviados ao programa')
	else
		LrDialogs.message('Álbum', tostring(resultado), 'critical')
	end
end)
