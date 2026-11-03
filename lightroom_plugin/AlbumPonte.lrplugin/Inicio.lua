-- Começa junto com o Lightroom: olha a pasta de pedidos a cada 3 segundos.

local LrTasks = import 'LrTasks'
local Ponte = require 'Ponte'

_G.albumPonteGeracao = (_G.albumPonteGeracao or 0) + 1
_G.albumPonteAtivo = true
local minhaGeracao = _G.albumPonteGeracao

LrTasks.startAsyncTask(function()
	LrTasks.pcall(Ponte.gravarPresets)
	while _G.albumPonteAtivo and _G.albumPonteGeracao == minhaGeracao do
		local ok, erro = LrTasks.pcall(Ponte.ciclo)
		if not ok then Ponte.registrar(erro) end
		LrTasks.sleep(3)
	end
end)
