# Validação de bancada — TX9

Esta lista registra o que ainda deve ser confirmado no equipamento físico.
Testes em um PC não validam o driver Wi-Fi da TX9.

## Verificações executadas no desenvolvimento

- 19 testes automatizados: portal, busca FTS sem acentos, filtro por disciplina,
  importação de texto, download do original, escape de HTML, geração de links
  Kiwix e resposta a detecção de portal.
- Downloads simulados: SHA-256, retomada Range, reinício quando o servidor ignora
  Range, corrupção, interrupção, orçamento e falta de espaço.
- Interface em navegador real (Edge), larguras de 1366 e 390 pixels: pesquisar,
  abrir uma atividade, conferir ausência de rolagem horizontal e layout de leitura.
- URLs, tamanhos e checksums das cinco coleções consultados no servidor oficial.
  Os livros completos não foram baixados no computador de desenvolvimento.
- Sintaxe dos scripts shell. A ativação de systemd/hostapd/DHCP não foi executada
  no Windows usado para o desenvolvimento.

## Pendências na TV Box

1. Confirmar modelo/arquitetura, Ubuntu/Armbian em boot e `iw phy` com modo AP.
2. Montar o cartão por UUID; validar fstab; reiniciar e conferir a montagem.
3. Instalar por Ethernet e observar que os downloads terminam antes da troca de função do rádio.
4. Confirmar `sudo saberlocal status`, página inicial, cinco coleções e `/healthz`.
5. Desconectar a internet e ligar um celular ao Wi-Fi. Conferir IP via DHCP,
   abertura por HTTP e pesquisa real de um termo em cada coleção.
6. Testar abertura automática em Android e iOS; registrar onde o endereço manual foi necessário.
7. Importar uma apostila PDF com texto, pesquisar uma palavra do seu interior e baixar o original.
8. Testar com 3–5 clientes, medir memória com `free -m` e observar tempos de busca.
   Aumentar a carga gradualmente; registrar o limite adequado à escola.
9. Reiniciar sem internet: rede, portal, materiais e livros devem voltar.
10. Desligar corretamente, retirar o cartão e iniciar: os serviços devem pular
    a inicialização, sem gravar o acervo na eMMC por engano. Recolocar e reiniciar.
11. Confirmar que SSH não está acessível pelo Wi-Fi dos alunos, permanecendo
    acessível pelo canal administrativo planejado.
12. Em bancada, testar `sudo saberlocal rollback` e a reconexão à configuração anterior.

Registre data, imagem do SO, kernel, driver, rádio utilizado, número de clientes
e resultados. Só depois desse teste considere a imagem apta para replicação nas escolas.
