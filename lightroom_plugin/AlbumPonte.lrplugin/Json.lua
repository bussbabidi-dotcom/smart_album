-- JSON simples (o Lightroom não traz um). Textos em UTF-8.

local Json = {}

local escapes = {
	['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
	['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
}

local function codificarTexto(s)
	return '"' .. s:gsub('[%c"\\]', function(c)
		return escapes[c] or string.format('\\u%04x', c:byte())
	end) .. '"'
end

local function ehLista(t)
	local n = 0
	for k in pairs(t) do
		if type(k) ~= 'number' then return false end
		n = n + 1
	end
	for i = 1, n do
		if t[i] == nil then return false end
	end
	return true, n
end

function Json.encode(valor)
	local tipo = type(valor)
	if tipo == 'nil' then return 'null' end
	if tipo == 'boolean' then return valor and 'true' or 'false' end
	if tipo == 'number' then
		if valor ~= valor or valor == math.huge or valor == -math.huge then return 'null' end
		if valor == math.floor(valor) and math.abs(valor) < 1e15 then return string.format('%d', valor) end
		return string.format('%.10g', valor)
	end
	if tipo == 'string' then return codificarTexto(valor) end
	if tipo == 'table' then
		local lista, n = ehLista(valor)
		local partes = {}
		if lista and n > 0 then
			for i = 1, n do partes[#partes + 1] = Json.encode(valor[i]) end
			return '[' .. table.concat(partes, ',') .. ']'
		end
		if lista then return '[]' end
		for k, v in pairs(valor) do
			partes[#partes + 1] = codificarTexto(tostring(k)) .. ':' .. Json.encode(v)
		end
		return '{' .. table.concat(partes, ',') .. '}'
	end
	return 'null'
end

local function utf8(codigo)
	if codigo < 0x80 then return string.char(codigo) end
	if codigo < 0x800 then
		return string.char(0xC0 + math.floor(codigo / 0x40), 0x80 + codigo % 0x40)
	end
	if codigo < 0x10000 then
		return string.char(0xE0 + math.floor(codigo / 0x1000), 0x80 + math.floor(codigo / 0x40) % 0x40, 0x80 + codigo % 0x40)
	end
	return string.char(0xF0 + math.floor(codigo / 0x40000), 0x80 + math.floor(codigo / 0x1000) % 0x40,
		0x80 + math.floor(codigo / 0x40) % 0x40, 0x80 + codigo % 0x40)
end

function Json.decode(texto)
	local pos = 1
	if texto:sub(1, 3) == '\239\187\191' then pos = 4 end

	local function erro(msg)
		error('JSON inválido (' .. msg .. ') na posição ' .. pos)
	end

	local function espacos()
		pos = texto:find('[^ \t\r\n]', pos) or (#texto + 1)
	end

	local valor

	local function lerTexto()
		pos = pos + 1
		local partes = {}
		while true do
			local c = texto:sub(pos, pos)
			if c == '' then erro('texto sem fim') end
			if c == '"' then pos = pos + 1; break end
			if c == '\\' then
				local e = texto:sub(pos + 1, pos + 1)
				if e == 'u' then
					local codigo = tonumber(texto:sub(pos + 2, pos + 5), 16)
					if not codigo then erro('\\u inválido') end
					pos = pos + 6
					if codigo >= 0xD800 and codigo <= 0xDBFF and texto:sub(pos, pos + 1) == '\\u' then
						local baixo = tonumber(texto:sub(pos + 2, pos + 5), 16)
						if baixo then
							codigo = 0x10000 + (codigo - 0xD800) * 0x400 + (baixo - 0xDC00)
							pos = pos + 6
						end
					end
					partes[#partes + 1] = utf8(codigo)
				else
					local mapa = { b = '\b', f = '\f', n = '\n', r = '\r', t = '\t', ['"'] = '"', ['\\'] = '\\', ['/'] = '/' }
					partes[#partes + 1] = mapa[e] or e
					pos = pos + 2
				end
			else
				local fim = texto:find('["\\]', pos) or (#texto + 1)
				partes[#partes + 1] = texto:sub(pos, fim - 1)
				pos = fim
			end
		end
		return table.concat(partes)
	end

	valor = function()
		espacos()
		local c = texto:sub(pos, pos)
		if c == '{' then
			pos = pos + 1
			local obj = {}
			espacos()
			if texto:sub(pos, pos) == '}' then pos = pos + 1; return obj end
			while true do
				espacos()
				if texto:sub(pos, pos) ~= '"' then erro('esperava chave') end
				local chave = lerTexto()
				espacos()
				if texto:sub(pos, pos) ~= ':' then erro('esperava :') end
				pos = pos + 1
				obj[chave] = valor()
				espacos()
				local s = texto:sub(pos, pos)
				pos = pos + 1
				if s == '}' then return obj end
				if s ~= ',' then erro('esperava , ou }') end
			end
		elseif c == '[' then
			pos = pos + 1
			local lista = {}
			espacos()
			if texto:sub(pos, pos) == ']' then pos = pos + 1; return lista end
			while true do
				lista[#lista + 1] = valor()
				espacos()
				local s = texto:sub(pos, pos)
				pos = pos + 1
				if s == ']' then return lista end
				if s ~= ',' then erro('esperava , ou ]') end
			end
		elseif c == '"' then
			return lerTexto()
		elseif texto:sub(pos, pos + 3) == 'true' then
			pos = pos + 4; return true
		elseif texto:sub(pos, pos + 4) == 'false' then
			pos = pos + 5; return false
		elseif texto:sub(pos, pos + 3) == 'null' then
			pos = pos + 4; return nil
		else
			local numero = texto:match('^-?%d+%.?%d*[eE]?[-+]?%d*', pos)
			if not numero or numero == '' then erro('valor inesperado') end
			pos = pos + #numero
			return tonumber(numero)
		end
	end

	return valor()
end

return Json
