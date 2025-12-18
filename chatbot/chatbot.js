const qrcode = require('qrcode-terminal');
const express = require('express');
const bodyParser = require('body-parser');
const path = require('path');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');

const app = express();
const port = 3000;

app.use(bodyParser.json());
app.use(bodyParser.urlencoded({ extended: true }));

// ================= CLIENTE WHATSAPP =================
const client = new Client({
    authStrategy: new LocalAuth({
        dataPath: path.join(__dirname, 'session')
    }),
    puppeteer: {
        args: ['--no-sandbox', '--disable-setuid-sandbox'],
        executablePath: process.env.CHROME_PATH || undefined
    }
});

// ================= QR CODE =================
client.on('qr', qr => {
    qrcode.generate(qr, { small: true });
});

// ================= READY =================
client.on('ready', () => {
    console.log('✅ WhatsApp conectado com sucesso!');
});

client.initialize();

// ================= FUNÇÕES AUX =================
function capitalizeFirst(str) {
    if (!str) return '';
    return str.charAt(0).toUpperCase() + str.slice(1).toLowerCase();
}

// ================= FLUXO PRINCIPAL =================
client.on('message', async msg => {
    const messageBody = msg.body?.trim() || '';
    const from = msg.from;
    const fromUser = from.endsWith('@c.us') || from.endsWith('@lid');

    console.log(`[FLOW] Mensagem recebida: "${messageBody}"`);

    // ===== BLOCO 1: SAUDAÇÃO =====
    const comandosSaudacao = /\b(menu|oi|olá|ola|ei|bom dia|boa tarde|boa noite|dia|tarde|noite)\b/i;

    if (comandosSaudacao.test(messageBody) && fromUser) {
        console.log('*** BLOCO 1: SAUDAÇÃO ***');

        try {
            // 🚨 NÃO USA getContact (BUG DO WHATSAPP)
            const rawName =
                msg._data?.notifyName ||
                msg._data?.sender?.pushname ||
                'usuário';

            const name = capitalizeFirst(rawName).split(' ')[0];

            await client.sendMessage(
                from,
                `Olá, ${name}! Eu sou o *RemedIAr*, seu assistente inteligente para consultas de medicamentos. 💊\n\n` +
                'Digite *1* para saber mais sobre mim.'
            );
        } catch (e) {
            console.error('[ERRO SAUDAÇÃO]', e.message);
        }
        return;
    }

    // ===== BLOCO 2: OPÇÃO 1 =====
    if (messageBody === '1' && fromUser) {
        console.log('*** BLOCO 2: OPÇÃO 1 ***');

        await client.sendMessage(
            from,
            '📌 *Como eu funciono?*\n\n' +
            '💬 *Consultas por Texto*\nPergunte sobre qualquer medicamento e eu respondo com base na bula oficial.\n\n' +
            '📸 *Consultas por Imagem*\nEnvie a foto da caixa de um remédio e eu identifico e explico.\n\n' +
            '⏰ *Lembretes de Medicamentos*\nDiga o remédio, horário e frequência que eu te aviso automaticamente.\n\n' +
            '📝 *Exemplo:*\nTomar dipirona a cada 6 horas começando às 08:00.'
        );
        return;
    }

    // ===== BLOCO 6: PROCESSAMENTO GERAL =====
    if (msg.hasMedia || (messageBody && fromUser)) {
        console.log('*** BLOCO 6: PROCESSAMENTO GERAL ***');

        // 🔹 Chat ID seguro
        let chatIdCompleto = from;
        try {
            const chat = await msg.getChat();
            chatIdCompleto = chat?.id?._serialized || from;
        } catch {}

        // 🔹 Telefone limpo
        let telefone_usuario = from.split('@')[0].replace(/\D/g, '');
        if (!telefone_usuario.startsWith('55')) {
            telefone_usuario = '55' + telefone_usuario;
        }
        telefone_usuario = telefone_usuario.slice(0, 13);

        console.log(`[DEBUG] Telefone: ${telefone_usuario}`);
        console.log(`[DEBUG] ChatID: ${chatIdCompleto}`);

        let payload = {
            mensagem: messageBody,
            isMedia: false,
            mimeType: 'text/plain',
            telefone_usuario,
            chat_id_completo: chatIdCompleto
        };

        // 🔹 Se for mídia
        if (msg.hasMedia) {
            try {
                const media = await msg.downloadMedia();
                payload = {
                    mensagem: '',
                    isMedia: true,
                    mimeType: media.mimetype,
                    mediaData: media.data,
                    telefone_usuario,
                    chat_id_completo: chatIdCompleto
                };
            } catch (e) {
                console.error('[ERRO MEDIA]', e.message);
                await client.sendMessage(from, '❌ Não consegui processar a imagem.');
                return;
            }
        }

        // 🔹 Envia para o Flask
        try {
            const response = await fetch('http://flask_api:5000/bula/consultar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (data.status === 'success') {
                let resposta = data.resposta || '⚠️ Resposta vazia.';
                resposta = resposta.replace(/(\n\s*\n)/gm, '\n\n').trim();

                if (resposta.length > 3500) {
                    resposta = resposta.substring(0, 3500) + '... (resposta cortada)';
                }

                await client.sendMessage(from, resposta);
            } else {
                await client.sendMessage(from, data.resposta || '⚠️ Erro no servidor.');
            }
        } catch (e) {
            console.error('[ERRO FLASK]', e.message);
            await client.sendMessage(from, '⚠️ Serviço offline. Tente novamente mais tarde.');
        }
        return;
    }

    console.log(`[FLOW] Mensagem não tratada: "${messageBody}"`);
});

// ================= ROTA DE LEMBRETES =================
app.post('/enviar_lembrete', async (req, res) => {
    const { to, message, image_url } = req.body;

    if (!to || !message) {
        return res.status(400).json({ status: 'error', message: 'Campos obrigatórios ausentes.' });
    }

    try {
        if (!client.info || !client.info.wid) {
            return res.status(503).json({ status: 'error', message: 'WhatsApp não está pronto.' });
        }

        const numero =
            to.includes('@') ? to :
            to.replace(/\D/g, '').startsWith('55')
                ? to.replace(/\D/g, '') + '@c.us'
                : '55' + to.replace(/\D/g, '') + '@c.us';

        if (image_url) {
            const media = await MessageMedia.fromUrl(image_url, { unsafeMime: true });
            await client.sendMessage(numero, media, { caption: message });
        } else {
            await client.sendMessage(numero, message);
        }

        return res.json({ status: 'success' });
    } catch (e) {
        console.error('[ERRO LEMBRETE]', e.message);
        return res.status(500).json({ status: 'error', message: e.message });
    }
});

// ================= START SERVER =================
app.listen(port, () => {
    console.log(`🚀 Node rodando na porta ${port}`);
});
