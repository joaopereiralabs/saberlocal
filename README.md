# SABERLOCAL — biblioteca escolar offline

Uma TV Box TX9 com Ubuntu/Armbian vira um ponto de acesso Wi-Fi com uma biblioteca
para **ensino fundamental e médio**. O aluno conecta, abre o portal e pesquisa
conteúdos guardados no cartão, sem depender da internet.

**Estado:** implementação inicial. Portal e rotinas têm testes automatizados;
instalação completa, driver SSV6051, alcance Wi-Fi e carga precisam ser validados
na TX9 do projeto antes de levar às escolas.

## O que vem pronto

- Portal em português, adaptado a celulares, sem fontes, scripts ou serviços externos.
- Pesquisa nos materiais da escola com SQLite FTS5, inclusive sem acentos.
- Coleções em português de matemática, física, história, geografia e Wikilivros.
- Busca dentro de cada coleção pelo Kiwix. A busca de apostilas e a de artigos
  aparecem como ações distintas na interface; não há índice unificado entre elas.
- Importação de apostilas e atividades PDF, TXT e Markdown pelo responsável.
- Wi-Fi WPA2, DNS e DHCP locais, auxílio à abertura automática da página e início no boot.
- Verificação SHA-256, retomada de downloads e limite de armazenamento.
- Comandos para diagnóstico e remoção das configurações de rede do projeto.

Não há chatbot ou modelo de IA: a pesquisa consulta o acervo instalado.
As coleções públicas não são uma sequência didática nem garantem alinhamento à BNCC.
A equipe pedagógica seleciona os artigos apropriados a cada turma. Português,
biologia e outras disciplinas podem receber materiais dos professores e livros do acervo.

## Equipamento e preparação

- TX9 já iniciando Ubuntu/Armbian, com `systemd` e acesso `sudo`.
- 1 GB de RAM; 8 GB internos para o sistema e cartão de 32 GB para o acervo.
- Internet **durante a instalação**, preferencialmente por Ethernet.
- Rádio Wi-Fi com driver funcionando e **modo AP** habilitado.
- Cartão com sistema de arquivos Linux (ext4 recomendado), montado de modo persistente.

O projeto do professor documenta hardware, DTB e driver:
[rbeninca/tvbox](https://github.com/rbeninca/tvbox).
O [guia de hotspot](https://github.com/rbeninca/tvbox/tree/main/hotspot) registra
que o SSV6051 pode precisar de patches para AP. O instalador verifica o anúncio
de suporte pelo `iw`; o sucesso real só pode ser confirmado criando a rede no aparelho.
Ele não altera kernel, DTB, bootloader ou driver. Uma alternativa é um adaptador
Wi-Fi USB cujo driver Linux suporte AP.

Faça a instalação por cabo Ethernet ou console: o rádio selecionado passará a
ser o ponto de acesso e deixará de funcionar como cliente do Wi-Fi anterior.
Não se usa o mesmo rádio simultaneamente para baixar da internet e atender os alunos.

### Montar o cartão uma vez

Se a escola já tem o cartão montado, use seu caminho em `--data-dir`.
Para identificar as partições existentes:

```bash
lsblk -f
```

Use o UUID da **partição de dados do cartão**, conferido nesse resultado.
Os comandos abaixo **não formatam** o cartão. Substitua `SEU_UUID` pelo valor real:

```bash
sudo mkdir -p /mnt/saberlocal
sudo mount /dev/disk/by-uuid/SEU_UUID /mnt/saberlocal
```

Para montar no boot, acrescente ao `/etc/fstab`, sem remover as linhas existentes:

```fstab
UUID=SEU_UUID /mnt/saberlocal ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2
```

Essa linha pressupõe uma partição ext4 existente. Confira o tipo com `lsblk -f`.
Valide a configuração com `sudo findmnt --verify` e `findmnt /mnt/saberlocal`.
Caso o cartão ainda precise ser particionado/formatado, prepare-o com o responsável
do laboratório antes de instalar: este projeto não apaga discos automaticamente.

## Instalar com um comando

Baixe e extraia este repositório na TV Box. Entre na pasta `saberlocal` e execute:

```bash
sudo bash install.sh
```

Ou, com Git já instalado:

```bash
git clone https://github.com/joaopereiralabs/saberlocal.git
cd saberlocal
sudo bash install.sh
```

O padrão é `/mnt/saberlocal/acervo`. Com apenas um rádio, ele é identificado
automaticamente. Para selecionar caminhos e rádio explicitamente:

```bash
sudo bash install.sh --interface wlan0 --data-dir /mnt/saberlocal/acervo
```

O instalador baixa pacotes Ubuntu e aproximadamente **1,18 GiB** de livros
(edições de outubro de 2026). A transferência completa ocupa esse espaço no cartão;
os ZIM são lidos compactados, sem extração. Há limite padrão de 20 GiB de ZIM e
reserva mínima de 512 MiB no volume de dados. Importações também consomem espaço.
O sistema precisa de pelo menos 800 MiB livres antes da instalação dos pacotes.

Ao concluir, ele informa:

- Rede: `SABERLOCAL`.
- Senha WPA2 gerada para esta instalação.
- Página: **http://10.42.0.1**.

A senha também fica em `/etc/saberlocal/private.json`, legível somente por root.
Anote-a para informar aos alunos. Reinstalar preserva a senha existente, os livros
verificados e os documentos importados. Downloads interrompidos podem ser retomados
executando o instalador novamente. Uma falha de download acontece antes da ativação da rede.

### Opções

```bash
# Somente verificar os requisitos, quando iw e demais ferramentas já estão instalados:
sudo bash install.sh --check --interface wlan0

# Instalar portal e rede com guias iniciais, sem baixar coleções:
sudo bash install.sh --no-content

# Preparar configurações sem ativar serviços nem desconectar o rádio:
sudo bash install.sh --no-activate

# Autorizar explicitamente dados na partição raiz, por exemplo se o SO está no SD:
sudo bash install.sh --allow-internal --data-dir /srv/saberlocal
```

`--no-activate` ainda instala pacotes e grava exceções para os gerenciadores de rede.
O terminal informa o comando para ativação posterior; para restaurar as exceções,
use `sudo saberlocal rollback`. `--check` não instala pacotes nem grava configurações.

### Personalização

Copie `config.example.json` para `config.local.json`. Edite nome da escola,
mensagem inicial, SSID e, se desejar, senha de 8 a 63 caracteres ASCII:

```bash
sudo bash install.sh --config config.local.json
```

Não publique `config.local.json`: ele pode conter a senha. A senha nunca é enviada
ao portal. O nome da rede aceita até 32 bytes. Use canais entre 1 e 11 no Brasil.

## Acrescentar materiais dos professores

No terminal da TV Box, com o arquivo já copiado para o aparelho:

```bash
sudo saberlocal import /caminho/atividade.pdf \
  --title 'Matemática — 7º ano — Frações' \
  --category 'Matemática' \
  --source 'Professora Ana — Escola Exemplo' \
  --license 'Uso autorizado pela autora para esta biblioteca' \
  --date '2026-11-04'
```

Os campos de exemplo devem ser substituídos pelos dados reais. O texto torna-se
pesquisável e o arquivo original fica disponível para download. Categorias do
portal: Matemática, Ciências, História, Geografia, Língua portuguesa, Tecnologia e
Orientações. Outras categorias continuam visíveis em Todos.

Limites: 100 MiB por arquivo e 5 MiB de texto extraído. PDF escaneado sem camada
de texto precisa de OCR prévio. Markdown é mostrado como texto seguro, sem executar
HTML. A importação é administrativa, pelo terminal; alunos não enviam arquivos
nem alteram documentos pela página. Importe apenas materiais autorizados e sem
dados pessoais de estudantes.

## Como o aluno acessa

1. Conecte ao Wi-Fi SABERLOCAL usando a senha fornecida pela escola.
2. Se surgir o aviso de rede sem internet, escolha permanecer conectado.
3. Se o portal não abrir, acesse **http://10.42.0.1**, incluindo `http://`.
4. Pesquise uma palavra. Os materiais da escola aparecem abaixo; cada cartão
   oferece a pesquisa de artigos na respectiva coleção.

O portal responde aos pedidos HTTP comuns de detecção de rede. A abertura
automática varia entre Android, iOS e outros sistemas; não é garantida. HTTPS,
VPN e DNS privado não podem ser interceptados de forma transparente. Para esses
casos, use o endereço HTTP e ajuste temporariamente o celular com ajuda da escola.

## Operação e diagnóstico

```bash
sudo saberlocal status
sudo saberlocal logs

# Completar/verificar as coleções do manifesto e reiniciar Kiwix:
sudo saberlocal download

# Conferir portal pelo próprio aparelho:
curl http://10.42.0.1/healthz

# Verificar a rede criada:
iw dev
ip -4 address
```

Serviços: `saberlocal-network`, `saberlocal-hostapd`, `saberlocal-dns`,
`saberlocal-portal`, `saberlocal-kiwix` e `saberlocal-web`.
Conteúdo em `/mnt/saberlocal/acervo`; aplicativo em `/opt/saberlocal`;
configurações em `/etc/saberlocal`. Se o cartão faltar, os serviços que dependem
dele não iniciam. Os comandos administrativos também verificam a montagem.

O acervo é fixado por URL e SHA-256 em `content/packs.json`: executar download
verifica as mesmas edições, não escolhe silenciosamente uma versão nova. Para
atualizar, a equipe deve revisar um novo ZIM no catálogo oficial, obter seu SHA-256,
alterar o manifesto e reinstalar o projeto. Arquivos de edições antigas não são
apagados automaticamente. Confira o espaço e remova-os manualmente quando seguro.

### Conflitos e rede

O projeto usa seu próprio nginx, sem substituir a configuração global, e seu
próprio dnsmasq. As portas 80, 8081 e 8090 precisam estar livres na primeira
instalação. DNS/DHCP existentes não podem ocupar indiscriminadamente as portas
necessárias ao rádio. Desative um hotspot anterior nesse rádio antes de instalar.
A Ethernet deve estar em uma rede diferente de `10.42.0.0/24`.

O instalador cria exceções do NetworkManager/systemd-networkd para o rádio escolhido.
Não reinicia esses gerenciadores globalmente. Os alunos acessam somente DNS,
DHCP e HTTP na TV Box; o firewall próprio bloqueia SSH pelo Wi-Fi e encaminhamento
para outras redes. A administração permanece no console ou na Ethernet.
Não limpa regras de firewall de outros projetos. Se UFW ou outro firewall estiver
ativo, o responsável precisa permitir no rádio as portas TCP 80/53 e UDP 53/67;
as permissões deste projeto não anulam bloqueios impostos por outros firewalls.
O isolamento entre clientes depende de suporte efetivo do rádio/driver.

Para desfazer as configurações de rede do projeto:

```bash
sudo saberlocal rollback
```

Isso desativa os serviços, remove a tabela `inet saberlocal` pelo serviço de rede
e restaura os arquivos externos salvos antes da instalação. O acervo, pacotes e
aplicativo são preservados. Pode ser necessário reconectar o Wi-Fi anterior.
Os backups ficam em `/var/lib/saberlocal-installer`.

## Arquitetura e limites

```text
Celular/computador
      │ Wi-Fi WPA2
      ▼
hostapd + dnsmasq ──► 10.42.0.1:80 (nginx próprio)
                         ├── portal Flask/Gunicorn ──► SQLite + documentos
                         └── /biblioteca/ ──────────► kiwix-serve + livros ZIM
```

Sem Docker, interface gráfica, banco de dados externo ou IA. Gunicorn usa um
processo e duas threads; Kiwix usa duas threads. Os limites de memória dos serviços
reduzem a chance de um processo consumir toda a RAM, mas não garantem capacidade
sob qualquer carga. O limite de 20 estações no hostapd é apenas um teto de
configuração: teste primeiro com 3–5 alunos, depois aumente e meça no equipamento.
Não há promessa de atender uma turma inteira simultaneamente com o rádio interno.

Não há conta de aluno, analytics, histórico de pesquisa ou logs de acesso HTTP.
Os logs de diagnóstico dos serviços e concessões DHCP ainda podem registrar
informações de rede. Artigos e livros preservam seus próprios créditos/licenças.

## Desenvolvimento e testes

Em outro computador, sem configurar Wi-Fi:

```bash
python -m pip install -r requirements.txt
python scripts/dev.py
```

Abra `http://127.0.0.1:8090`. Essa demonstração mostra os guias iniciais; não
simula rádio, DNS, DHCP ou coleções que não tenham sido baixadas.

```bash
python -m unittest discover -s tests -v
for script in install.sh scripts/kiwix.sh scripts/saberlocal; do bash -n "$script"; done
```

Consulte [VALIDACAO.md](VALIDACAO.md) para os testes de bancada pendentes.
Consulte [NOTICE.md](NOTICE.md) para as atribuições.

## Publicar este código no GitHub

O projeto pode ser enviado a um novo repositório chamado `saberlocal`. No
computador com Git e GitHub CLI (`gh`), autenticados na sua conta:

```bash
gh auth login
git init -b main
git add .
git commit -m "Implementa biblioteca escolar offline SABERLOCAL"
gh repo create saberlocal --private --source=. --remote=origin --push
```

Se o Git pedir nome/e-mail, configure sua identidade antes do commit. O exemplo
cria um repositório privado; use `--public` se quiser disponibilizá-lo publicamente.
Esses comandos devem ser executados dentro da pasta do projeto, sem arquivos
com senha. O `.gitignore` exclui configurações locais e dados de desenvolvimento.
