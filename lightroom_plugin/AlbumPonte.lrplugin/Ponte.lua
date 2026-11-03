-- Ponte entre o programa do álbum e o Lightroom, por arquivos em
-- %LOCALAPPDATA%\smart_album\lightroom (veja lightroom.py).

local LrApplication = import 'LrApplication'
local LrExportSession = import 'LrExportSession'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'
local LrLogger = import 'LrLogger'

local Json = require 'Json'

local log = LrLogger('AlbumPonte')
log:enable('logfile')

local Ponte = {}

function Ponte.registrar(texto)
	log:info(tostring(texto))
end

function Ponte.pastas()
	local home = LrPathUtils.getStandardFilePath('home')
	local base = LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(home, 'AppData'), 'Local'), 'smart_album'), 'lightroom')
	local p = {
		base = base,
		pedidos = LrPathUtils.child(base, 'pedidos'),
		status = LrPathUtils.child(base, 'status'),
	}
	for _, pasta in pairs(p) do
		LrFileUtils.createAllDirectories(pasta)
	end
	return p
end

local function gravar(caminho, texto)
	local temp = caminho .. '.tmp'
	local f = assert(io.open(temp, 'wb'))
	f:write(texto)
	f:close()
	if LrFileUtils.exists(caminho) then
		LrFileUtils.delete(caminho)
	end
	LrFileUtils.move(temp, caminho)
end

local function lerJson(caminho)
	local texto = LrFileUtils.readFile(caminho)
	if not texto or texto == '' then return nil end
	return Json.decode(texto)
end

function Ponte.marcarVivo()
	gravar(LrPathUtils.child(Ponte.pastas().base, 'vivo.txt'), 'ok')
end

function Ponte.gravarPresets()
	local lista = {}
	for _, pasta in ipairs(LrApplication.developPresetFolders()) do
		for _, preset in ipairs(pasta:getDevelopPresets()) do
			lista[#lista + 1] = { nome = pasta:getName() .. ' / ' .. preset:getName(), uuid = preset:getUuid() }
		end
	end
	gravar(LrPathUtils.child(Ponte.pastas().base, 'presets.json'), Json.encode({ presets = lista }))
	return #lista
end

local function comAcesso(catalogo, nome, funcao)
	local ok, erro = LrTasks.pcall(function()
		catalogo:withWriteAccessDo(nome, funcao, { timeout = 60 })
	end)
	if not ok then error(erro) end
end

-- Lotes sem nomes repetidos (o Lightroom exporta com o nome da original)
-- e com um formato só.
local function lotes(itens)
	local saida = {}
	for _, item in ipairs(itens) do
		if item.foto then
			local nome = string.lower(LrPathUtils.removeExtension(LrPathUtils.leafName(item.caminho)))
			local colocado = false
			for _, lote in ipairs(saida) do
				if lote.formato == item.formato and not lote.nomes[nome] then
					lote.nomes[nome] = true
					lote.itens[#lote.itens + 1] = item
					colocado = true
					break
				end
			end
			if not colocado then
				saida[#saida + 1] = { formato = item.formato, nomes = { [nome] = true }, itens = { item } }
			end
		end
	end
	return saida
end

local function configuracaoExportacao(pasta, formato)
	return {
		LR_export_destinationType = 'specificFolder',
		LR_export_destinationPathPrefix = pasta,
		LR_export_useSubfolder = false,
		LR_format = formato,
		LR_jpeg_quality = 1,
		LR_export_colorSpace = 'sRGB',
		LR_export_bitDepth = 8,
		LR_tiff_compressionMethod = 'compressionMethod_None',
		LR_size_doConstrain = false,
		LR_outputSharpeningOn = false,
		LR_collisionHandling = 'overwrite',
		LR_renamingTokensOn = false,
		LR_reimportExportedPhoto = false,
		LR_minimizeEmbeddedMetadata = false,
		LR_removeLocationMetadata = false,
		LR_includeVideoFiles = false,
		LR_export_postProcessing = 'doNothing',
	}
end

function Ponte.processarPedido(arquivo)
	local p = Ponte.pastas()
	local andamento = arquivo .. '.processando'
	if not LrFileUtils.move(arquivo, andamento) then return end
	local pedido = lerJson(andamento)
	if not pedido then
		LrFileUtils.delete(andamento)
		return
	end

	local status = {
		id = pedido.id, fim = false, feitas = 0, total = #(pedido.fotos or {}),
		pasta_temp = pedido.pasta_temp, fotos = {},
	}
	local caminhoStatus = LrPathUtils.child(p.status, 'status_' .. pedido.id .. '.json')
	local function gravarStatus() gravar(caminhoStatus, Json.encode(status)) end
	gravarStatus()

	local ok, erroGeral = LrTasks.pcall(function()
		local catalogo = LrApplication.activeCatalog()
		local preset = LrApplication.developPresetByUuid(pedido.preset_uuid)
		if not preset then
			error('Preset não encontrado: ' .. tostring(pedido.preset_nome))
		end

		-- item: dados internos (foto do catálogo, revelação de antes);
		-- item.publico: o que vai para o arquivo de status.
		local itens = {}
		for _, f in ipairs(pedido.fotos or {}) do
			local publico = { caminho = f.caminho, ok = false }
			local item = { caminho = f.caminho, formato = f.formato or 'JPEG', publico = publico }
			itens[#itens + 1] = item
			status.fotos[#status.fotos + 1] = publico
			local foto = catalogo:findPhotoByPath(f.caminho)
			if not foto then
				local okImport, erroImport = LrTasks.pcall(function()
					comAcesso(catalogo, 'Álbum: importar foto', function()
						foto = catalogo:addPhoto(f.caminho)
					end)
				end)
				if not okImport then publico.erro = 'não importou: ' .. tostring(erroImport) end
			end
			item.foto = foto
		end

		for _, item in ipairs(itens) do
			if item.foto then
				item.antes = item.foto:getDevelopSettings()
				local okPreset, erroPreset = LrTasks.pcall(function()
					comAcesso(catalogo, 'Álbum: aplicar preset', function()
						item.foto:applyDevelopPreset(preset, _PLUGIN)
					end)
				end)
				if not okPreset then
					item.publico.erro = 'preset: ' .. tostring(erroPreset)
					item.foto = nil
				end
			end
		end

		for n, lote in ipairs(lotes(itens)) do
			local pasta = LrPathUtils.child(pedido.pasta_temp, 'lote_' .. n)
			LrFileUtils.createAllDirectories(pasta)
			local porId = {}
			local fotos = {}
			for _, item in ipairs(lote.itens) do
				porId[item.foto.localIdentifier] = item
				fotos[#fotos + 1] = item.foto
			end
			local sessao = LrExportSession({
				photosToExport = fotos,
				exportSettings = configuracaoExportacao(pasta, lote.formato),
			})
			for _, rendition in sessao:renditions({ stopIfCanceled = true }) do
				local okRender, caminhoOuErro = rendition:waitForRender()
				local item = porId[rendition.photo.localIdentifier]
				if item then
					if okRender then
						item.publico.saida = caminhoOuErro
						item.publico.ok = true
					else
						item.publico.erro = tostring(caminhoOuErro)
					end
				end
				status.feitas = status.feitas + 1
				gravarStatus()
			end
		end

		-- Devolve a revelação de antes: o arquivo vai ser trocado pela versão
		-- já tratada, e o preset não pode ser aplicado de novo em cima dela.
		for _, item in ipairs(itens) do
			if item.foto and item.antes then
				local okVolta, erroVolta = LrTasks.pcall(function()
					comAcesso(catalogo, 'Álbum: devolver revelação', function()
						item.foto:applyDevelopSettings(item.antes)
					end)
				end)
				if not okVolta then Ponte.registrar('devolver revelação: ' .. tostring(erroVolta)) end
			end
		end
	end)

	if not ok then
		status.erro = tostring(erroGeral)
	end
	status.fim = true
	gravarStatus()
	LrFileUtils.delete(andamento)
end

-- Depois que o programa trocou os arquivos: pede miniaturas novas ao Lightroom.
Ponte.pedidosDeMiniatura = {}

function Ponte.atualizarFotos(arquivo)
	local dados = lerJson(arquivo)
	LrFileUtils.delete(arquivo)
	if not dados then return end
	local catalogo = LrApplication.activeCatalog()
	for _, caminho in ipairs(dados.fotos or {}) do
		local foto = catalogo:findPhotoByPath(caminho)
		if foto then
			local pedido = foto:requestJpegThumbnail(256, 256, function() end)
			Ponte.pedidosDeMiniatura[#Ponte.pedidosDeMiniatura + 1] = pedido
		end
	end
	while #Ponte.pedidosDeMiniatura > 200 do
		table.remove(Ponte.pedidosDeMiniatura, 1)
	end
end

function Ponte.ciclo()
	local p = Ponte.pastas()
	Ponte.marcarVivo()
	local arquivos = {}
	for arquivo in LrFileUtils.files(p.pedidos) do
		arquivos[#arquivos + 1] = arquivo
	end
	table.sort(arquivos)
	for _, arquivo in ipairs(arquivos) do
		local nome = LrPathUtils.leafName(arquivo)
		if nome:match('^presets_.+%.json$') then
			LrFileUtils.delete(arquivo)
			Ponte.gravarPresets()
		elseif nome:match('^pedido_.+%.json$') then
			Ponte.processarPedido(arquivo)
		elseif nome:match('^atualizar_.+%.json$') then
			Ponte.atualizarFotos(arquivo)
		end
	end
end

return Ponte
