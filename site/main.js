// Picks the download button that fits the visitor's system. Without JavaScript the buttons all still work.
(function () {
  var ua = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
  var agent = navigator.userAgent || "";
  var os = /win/i.test(ua) || /Windows/i.test(agent) ? "windows"
    : /mac/i.test(ua) || /Macintosh/i.test(agent) ? "mac"
    : /linux|x11/i.test(ua + agent) && !/Android/i.test(agent) ? "linux" : "";
  var names = { windows: "Windows", mac: "macOS", linux: "Linux" };
  var base = "https://github.com/Youssef-Mekkawy/VidCatch/releases/latest/download/";
  var files = { windows: "VidCatch-windows.zip", mac: "VidCatch-macos.tar.gz", linux: "VidCatch-linux.tar.gz" };
  if (!os) return;
  var cta = document.getElementById("cta-main");
  if (cta) { cta.textContent = "Download for " + names[os]; cta.href = base + files[os]; }
  var card = document.querySelector('.dl .card[data-os="' + os + '"]');
  if (card) {
    card.setAttribute("data-match", "true");
    var tag = card.querySelector(".tag");
    if (tag) tag.hidden = false;
  }
})();
