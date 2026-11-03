local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local Ponte = require 'Ponte'

LrTasks.startAsyncTask(function()
	local ok, erro = LrTasks.pcall(Ponte.ciclo)
	if ok then
		LrDialogs.showBezel('Álbum: pedidos processados')
	else
		LrDialogs.message('Álbum', tostring(erro), 'critical')
	end
end)
