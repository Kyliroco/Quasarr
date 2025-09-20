import os
import sys
import unittest
from base64 import urlsafe_b64decode
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from quasarr.downloads.sources import me as download_me
from quasarr.providers.shared_state import convert_to_mb as real_convert_to_mb
from quasarr.search.sources import me


MAISON_SEARCH_HTML = """
<div id="dle-content">
                                        <div class="onetitle">Films 
    </div>

    <div class="dpad radial infoblock">
        <h1 class="heading">Résultat de la recherche</h1>
        <div id="searchtable" name="searchtable" class="searchtable">
            <form method="GET" role="search" data-dashlane-rid="de6d06d42a2e7190">
                
                    
                        
                            <div style="margin:10px;">
                                <input name="search" value="chien" maxlength="32" class="textin" style="width:250px" type="text" data-dashlane-rid="16b7fb8c8643e81d">
                                <select name="p" class="form-control" style="width: 25%;display: inline-block;" data-dashlane-rid="280aec6301c6cdec">
                                                                            <option selected="" value="films">Films</option>
                                                                            <option value="series">Séries</option>
                                                                            <option value="jeux">Jeux</option>
                                                                            <option value="musiques">Musiques</option>
                                                                            <option value="mangas">Animés</option>
                                                                            <option value="ebooks">Ebooks</option>
                                                                            <option value="logiciels">Logiciels</option>
                                                                            <option value="mobiles">Mobiles</option>
                                                                            <option value="autres-videos">Emissions TV</option>
                                                                    </select>
                                <br><br>
                                <input class="bbcodes" value="Rechercher" type="submit" data-dashlane-rid="38858e0993546307">
                            </div>
                        
                    
                
            </form>
        </div>
    </div>
    
    <br>
    <div class="navigation" align="center">
        <span>Précédent</span>                     <span href="?p=films&amp;search=chien&amp;page=1">1</span>                     <a href="?p=films&amp;search=chien&amp;page=2">2</a>                     <a href="?p=films&amp;search=chien&amp;page=3">3</a>                     <a href="?p=films&amp;search=chien&amp;page=4">4</a>                     <a href="?p=films&amp;search=chien&amp;page=5">5</a>                     <a href="?p=films&amp;search=chien&amp;page=6">6</a>                 <a href="?p=films&amp;search=chien&amp;page=2" rel="next">Suivant</a>    </div>

            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>27 janvier 2017</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=186-gabe-un-amour-de-chien"><img class="mainimg" data-newsid="880" src="/img/films/46f36975117644d12fd5ba469fc5e844.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=186-gabe-un-amour-de-chien">gabe un amour de chien</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>DVDRIP</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>31 août 2018</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=5558-chien"><img class="mainimg" data-newsid="880" src="/img/films/7597c76be86f603881b8674ef2c8441f.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=5558-chien">Chien</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>31 août 2018</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=5560-chien"><img class="mainimg" data-newsid="880" src="/img/films/61fdb4cb66d7e188aed1c3764ce827dd.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=5560-chien">Chien</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 720p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>31 août 2018</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=5561-chien"><img class="mainimg" data-newsid="880" src="/img/films/98a8bf5159b8c352460566b2f40ceb45.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=5561-chien">Chien</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 1080p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>16 novembre 2019</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=13637-temps-de-chien"><img class="mainimg" data-newsid="880" src="/img/films/7adeeca2b255f8aeb1184f36ca0cd153.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=13637-temps-de-chien">Temps de chien !</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>16 novembre 2019</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=13639-temps-de-chien"><img class="mainimg" data-newsid="880" src="/img/films/9a2260f50fff100112a39add878a7ec7.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=13639-temps-de-chien">Temps de chien !</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 720p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>16 novembre 2019</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=13640-temps-de-chien"><img class="mainimg" data-newsid="880" src="/img/films/2f389a7999d9de2942b46c2708586156.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=13640-temps-de-chien">Temps de chien !</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 1080p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>05 avril 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=16838-mon-chien-stupide"><img class="mainimg" data-newsid="880" src="/img/films/ea40211ec4f0fca415721c4c9d6508cb.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=16838-mon-chien-stupide">Mon chien Stupide</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>01 juillet 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=18308-nous-les-chiens"><img class="mainimg" data-newsid="880" src="/img/films/7f55301a7a593e4a6034fb9dddae39d1.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=18308-nous-les-chiens">Nous, Les Chiens</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP MD</b></span> <span style="color:#ffad0a"><b>(TRUEFRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>01 juillet 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=18309-nous-les-chiens"><img class="mainimg" data-newsid="880" src="/img/films/633ee569cea60db7d607a27b77dfc200.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=18309-nous-les-chiens">Nous, Les Chiens</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP MD 720p</b></span> <span style="color:#ffad0a"><b>(TRUEFRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>01 juillet 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=18310-nous-les-chiens"><img class="mainimg" data-newsid="880" src="/img/films/f59d03053dfba7fcdd2a501006524f9b.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=18310-nous-les-chiens">Nous, Les Chiens</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP MD 1080p</b></span> <span style="color:#ffad0a"><b>(TRUEFRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>18 septembre 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=19324-comme-chiens-et-chats-3-patte-dans-la-patte"><img class="mainimg" data-newsid="880" src="/img/films/2d4fd5f9d50f51d251e22d27865a3ede.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=19324-comme-chiens-et-chats-3-patte-dans-la-patte">Comme Chiens et Chats 3 : Patte dans la Patte</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>05 avril 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=16847-mon-chien-stupide"><img class="mainimg" data-newsid="880" src="/img/films/fa5d3e3dc0a7d0db27596338a21990ff.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=16847-mon-chien-stupide">Mon chien Stupide</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 720p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>05 avril 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=16848-mon-chien-stupide"><img class="mainimg" data-newsid="880" src="/img/films/06c786068abc8deaf9d6e313a35775e4.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=16848-mon-chien-stupide">Mon chien Stupide</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 1080p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>18 septembre 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=19329-comme-chiens-et-chats-3-patte-dans-la-patte"><img class="mainimg" data-newsid="880" src="/img/films/b41343e20f92300d07e47a1386f8a388.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=19329-comme-chiens-et-chats-3-patte-dans-la-patte">Comme Chiens et Chats 3 : Patte dans la Patte</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 720p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>18 septembre 2020</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=19330-comme-chiens-et-chats-3-patte-dans-la-patte"><img class="mainimg" data-newsid="880" src="/img/films/6bda35cd00bd92518e0bbb898270e444.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=19330-comme-chiens-et-chats-3-patte-dans-la-patte">Comme Chiens et Chats 3 : Patte dans la Patte</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 1080p</b></span> <span style="color:#ffad0a"><b>(MULTI (FRENCH))</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>26 janvier 2021</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=22694-chien-de-garde"><img class="mainimg" data-newsid="880" src="/img/films/f28c2df262e9f5b1c53ed173af8132aa.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=22694-chien-de-garde">Chien de Garde</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>27 janvier 2021</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=22697-chien-de-garde"><img class="mainimg" data-newsid="880" src="/img/films/9e1d8341e44dd5f26e7145b541e61470.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=22697-chien-de-garde">Chien de Garde</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 720p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>27 janvier 2021</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=22698-chien-de-garde"><img class="mainimg" data-newsid="880" src="/img/films/3ff33bfdce7b7c0769487ba2ad303db2.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=22698-chien-de-garde">Chien de Garde</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 1080p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>11 septembre 2022</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=34101-de-l-or-pour-les-chiens"><img class="mainimg" data-newsid="880" src="/img/films/f70b5716c16d290a3f0897a87d93b610.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=34101-de-l-or-pour-les-chiens">De l'or pour les chiens</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 720p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>11 septembre 2022</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=34105-de-l-or-pour-les-chiens"><img class="mainimg" data-newsid="880" src="/img/films/acc8070ff1d71157820e1a194b1795f0.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=34105-de-l-or-pour-les-chiens">De l'or pour les chiens</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>11 septembre 2022</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=34109-de-l-or-pour-les-chiens"><img class="mainimg" data-newsid="880" src="/img/films/94774df452d5bb43d642e0dad7a18de5.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=34109-de-l-or-pour-les-chiens">De l'or pour les chiens</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 1080p</b></span> <span style="color:#ffad0a"><b>(FRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>15 janvier 2023</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=37404-chien-blanc"><img class="mainimg" data-newsid="880" src="/img/films/38a923ca9ce32f3873851d53c2b0eb67.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=37404-chien-blanc">Chien blanc</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>HDRIP</b></span> <span style="color:#ffad0a"><b>(TRUEFRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>15 janvier 2023</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=37405-chien-blanc"><img class="mainimg" data-newsid="880" src="/img/films/bace20c785942114df04ad21cee02035.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=37405-chien-blanc">Chien blanc</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEBRIP 720p</b></span> <span style="color:#ffad0a"><b>(TRUEFRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
            <div class="cover_global" style="height:294px;">
            <div style="height:20px;color:#999999;border-bottom: 1px solid #e5e5e5;text-align:center">
                Publié le <time>15 janvier 2023</time>
            </div>
            <div style="height:193px;">
                <a href="?p=film&amp;id=37406-chien-blanc"><img class="mainimg" data-newsid="880" src="/img/films/2a1ca26e8e343059862dc1dbdc9b4da2.webp" width="145" height="193" border="0"></a>
            </div>
            <div style="height:14px;color:#808080;letter-spacing: 1px; clear:both;">
            </div>
            <div style="clear:both;">
                <div class="cover_infos_global">
                    <div class="cover_infos_title">
                        <a href="?p=film&amp;id=37406-chien-blanc">Chien blanc</a><br>
                        <span class="detail_release size_11"><span style="color:#1ba100"><b>WEB-DL 1080p</b></span> <span style="color:#ffad0a"><b>(TRUEFRENCH)</b></span></span><br>
                    </div>
                </div>
            </div>
        </div>
        
    <div class="navigation" align="center">
        <span>Précédent</span>                     <span href="?p=films&amp;search=chien&amp;page=1">1</span>                     <a href="?p=films&amp;search=chien&amp;page=2">2</a>                     <a href="?p=films&amp;search=chien&amp;page=3">3</a>                     <a href="?p=films&amp;search=chien&amp;page=4">4</a>                     <a href="?p=films&amp;search=chien&amp;page=5">5</a>                     <a href="?p=films&amp;search=chien&amp;page=6">6</a>                 <a href="?p=films&amp;search=chien&amp;page=2" rel="next">Suivant</a>    </div>
                                    </div>
"""


MAISON_FILM_DETAIL_HTML = """
<div class="corps">
            <div style="text-align: center;">
                                    <div class="otherversions" style="border-top: 0px solid #e6e6e6;text-align:left;">
                        <h3>Qualités également disponibles pour ce film:</h3>
                                                    <a href="?p=film&amp;id=5560-chien">
                                <span class="otherquality"><span style="color:#FE8903"><b>WEB-DL 720p</b></span>
                                <span style="color:#03AAFE"><b>(FRENCH)</b></span></span>
                            </a>
                                                    <a href="?p=film&amp;id=5561-chien">
                                <span class="otherquality"><span style="color:#FE8903"><b>WEB-DL 1080p</b></span>
                                <span style="color:#03AAFE"><b>(FRENCH)</b></span></span>
                            </a>
                                            </div>
                    <div class="smallsep"></div>
                                
                <div style="font-family: 'Ubuntu Condensed','Segoe UI',Verdana,Helvetica,sans-serif;font-size: 24px;letter-spacing: 0.05em;color: #ff4d00;font-weight: bold;text-align: center;margin: 25px;">Chien</div>
                <div style="font-size: 18px;margin: 10px auto;color:red;font-weight:bold;text-align:center;"> Qualité HDRIP | FRENCH</div>
                <center>
                    <img src="/img/films/7597c76be86f603881b8674ef2c8441f.webp" alt="Chien">
                    <br>
                    <a style="display: inline-block; font-size: 14px; font-weight: bold; color: #6060c5; text-decoration: underline; margin: 20px;" rel="nofollow noreferrer" href="https://dl-protect.link/rqts-url?fn=ZmlsbXN8NTU1OHwx">Télécharger Chien en HD</a>
                    <br>
                    <img src="/templates/zone/images/infos_film.png" alt="Chien"><br>
                    <br>
                    <strong><u>Origine</u> :</strong> France<br>
                    <strong><u>Durée</u> : </strong>01h34<br>
                    <strong><u>Année de production</u> :</strong> 2017<br>
                    <strong><u>Titre original</u> :</strong> Chien<br>
                    <img src="/templates/zone/images/liens.png" alt="Chien">
                </center>
            </div>
            <br><br>

            <a rel="nofollow noreferrer" href="https://dl-protect.link/rqts-url?fn=ZmlsbXN8NTU1OHwx"><h1 style="color:green; font-size: 12pt"><u><center><span id="selection_index16" class="selection_index"></span>Télécharger Chien PLUS RAPIDE (Téléchargement Sécurisé)</center></u></h1><br><br></a>
            
                            <center><font color="red">Chien.2017.FRENCH.HDRip.XviD.avi (698 Mo)</font></center><br>
                        
                            <h2 style="color: #007e92; font-size: 12pt"><b><center>Liens De Téléchargement :</center></b></h2>
                <br>
                
                <div style="text-align: center;"><a style="font-size: 15px; display: inline-block;" rel="nofollow noreferrer" href="https://dl-protect.link/rqts-url?fn=ZmlsbXN8NTU1OHwx"><span style="color: #4aa1ab;">PREMIUM</span><br><span style="color: black; font-weight: bold;">Telecharger</span><br><br></a></div>
            
                <div id="news-id-23557" style="display:inline;text-align:center;">
                    <center>
                        <div class="postinfo">
                                                                    <b><div style="font-weight:bold;color:#5390a8">Nitroflare</div></b>
                                        <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/867c36b4?fn=Q2hpZW4gW0hEUklQXSAtIEZSRU5DSA%3D%3D&amp;rl=a2">Télécharger</a></b><br>
                                    <br>
                                                                    <b><div style="font-weight:bold;color:#fbaf4e">Rapidgator</div></b>
                                        <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/6bc7c1b6?fn=Q2hpZW4gW0hEUklQXSAtIEZSRU5DSA%3D%3D&amp;rl=a2">Télécharger</a></b><br>
                                    <br>
                                                                    <b><div style="font-weight:bold;color:#000000">1fichier</div></b>
                                        <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/3cad5472?fn=Q2hpZW4gW0hEUklQXSAtIEZSRU5DSA%3D%3D&amp;rl=a2">Télécharger</a></b><br>
                                    <br>
                                                
                                            </div>
                    </center>
                </div>
                        
                            <br>
                <h2 style="color: #007e92; font-size: 12pt"><b><center>Liens De Streaming :</center></b></h2>
                <br>
                                <div id="news-id-23557" style="display:inline;text-align:center;">
                    <center>
                        <div class="postinfo">
                                                                    <b><div style="font-weight:bold;color:#fbaf4e">Streamango</div></b>
                                        <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/4a93afd4?fn=Q2hpZW4gW0hEUklQXSAtIEZSRU5DSA%3D%3D&amp;rl=a1">Regarder</a></b><br>
                                    <br>
                                            </div>
                    </center>
                </div>
                        
        </div>
"""


MAISON_FILM_DETAIL_HTML_WITH_EXTRA_HOSTERS = """
<div class="corps">
    <div style="text-align: center;">
        <div style="font-family: 'Ubuntu Condensed','Segoe UI',Verdana,Helvetica,sans-serif;font-size: 24px;letter-spacing: 0.05em;color: #ff4d00;font-weight: bold;text-align: center;margin: 25px;">Sebastian</div>
        <div style="font-size: 18px;margin: 10px auto;color:red;font-weight:bold;text-align:center;"> Qualité WEB-DL 1080p | VOSTFR</div>
        <center>
            <img src="/img/films/3ba354f710d53c6d667acc96b473ec25.webp" alt="Sebastian">
            <br>
            <a style="display: inline-block; font-size: 14px; font-weight: bold; color: #6060c5; text-decoration: underline; margin: 20px;" rel="nofollow noreferrer" href="https://dl-protect.link/rqts-url?fn=ZmlsbXN8NTIyNzR8MQ==">Télécharger Sebastian en HD</a>
            <br>
            <img src="/templates/zone/images/infos_film.png" alt="Sebastian"><br>
            <br>
            <strong><u>Origine</u> :</strong> Royaume-Uni<br>
            <strong><u>Durée</u> :</strong> 01h50<br>
            <strong><u>Année de production</u> :</strong> 2024<br>
            <strong><u>Titre original</u> :</strong> Sebastian<br>
        </center>
    </div>
    <br><br>
    <a rel="nofollow noreferrer" href="https://dl-protect.link/rqts-url?fn=ZmlsbXN8NTIyNzR8MQ=="><h1 style="color:green; font-size: 12pt"><u><center>Télécharger Sebastian PLUS RAPIDE (Téléchargement Sécurisé)</center></u></h1><br><br></a>
    <center><font color="red">Sebastian.2024.VOSTFR.1080p.WEB-DL.x264-Slay3R.mkv (2.9 Go)</font></center><br>
    <h2 style="color: #007e92; font-size: 12pt"><b><center>Liens De Téléchargement :</center></b></h2>
    <br>
    <div style="text-align: center;"><a style="font-size: 15px; display: inline-block;" rel="nofollow noreferrer" href="https://dl-protect.link/rqts-url?fn=ZmlsbXN8NTIyNzR8MQ=="><span style="color: #4aa1ab;">PREMIUM</span><br><span style="color: black; font-weight: bold;">Telecharger</span><br><br></a></div>
    <div id="news-id-23557" style="display:inline;text-align:center;">
        <center>
            <div class="postinfo">
                <b><div style="font-weight:bold;color:#c442b5">DailyUploads</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/81471d92?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a2">Télécharger</a></b><br>
                <br>
                <b><div style="font-weight:bold;color:#c2107b">Uploady</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/8a8aa521?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a2">Télécharger</a></b><br>
                <br>
                <b><div style="font-weight:bold;color:#f47445">Turbobit</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/4befd591?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a2">Télécharger</a></b><br>
                <br>
                <b><div style="font-weight:bold;color:#fbaf4e">Rapidgator</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/ebdb67ca?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a2">Télécharger</a></b><br>
                <br>
                <b><div style="font-weight:bold;color:#5390a8">Nitroflare</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/9f8235ae?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a2">Télécharger</a></b><br>
                <br>
                <b><div style="font-weight:bold;color:#000000">1fichier</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/636a3acb?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a2">Télécharger</a></b><br>
                <br>
            </div>
        </center>
    </div>
    <br>
    <h2 style="color: #007e92; font-size: 12pt"><b><center>Liens De Streaming :</center></b></h2>
    <br>
    <div id="news-id-23557" style="display:inline;text-align:center;">
        <center>
            <div class="postinfo">
                <b><div style="font-weight:bold;color:#f47445">Netu</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/45168829?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a1">Regarder</a></b><br>
                <br>
                <b><div style="font-weight:bold;color:#43a047">Vidoza</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/61f9f532?fn=U2ViYXN0aWFuIFtXRUItREwgMTA4MHBdIC0gVk9TVEZS&amp;rl=a1">Regarder</a></b><br>
                <br>
            </div>
        </center>
    </div>
</div>
"""


MAISON_MANGA_DETAIL_HTML = """
<div class="corps">
    <div style="text-align: center;">
        <center>
            <img src="/img/mangas/example.webp" alt="Example Anime">
        </center>
    </div>
    <h2 style="color: #007e92; font-size: 12pt"><b><center>Liens De Téléchargement :</center></b></h2>
    <div id="news-id-23557" style="display:inline;text-align:center;">
        <center>
            <div class="postinfo">
                <b><div style="font-weight:bold;color:#c442b5">DailyUploads</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/du-0?rl=h2">Episode 0</a></b><br>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/du-1?rl=h2">Episode 1</a></b><br>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/du-2?rl=h2">Episode 2</a></b><br>
                <br>
                <b><div style="font-weight:bold;color:#fbaf4e">Rapidgator</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/rg-0?rl=h2">Episode 0</a></b><br>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/rg-1?rl=h2">Episode 1</a></b><br>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/rg-2?rl=h2">Episode 2</a></b><br>
            </div>
        </center>
    </div>
    <h2 style="color: #007e92; font-size: 12pt"><b><center>Liens De Streaming :</center></b></h2>
    <div id="news-id-23557" style="display:inline;text-align:center;">
        <center>
            <div class="postinfo">
                <b><div style="font-weight:bold;color:#f47445">Netu</div></b>
                <b><a rel="external nofollow" target="_blank" href="https://dl-protect.link/stream-0?rl=h1">Episode 0</a></b><br>
            </div>
        </center>
    </div>
</div>
"""


class DummyConfigSection:
    def __init__(self, domain):
        self._initial_domain = domain
        self.saved = {}

    def get(self, key):
        return self.saved.get(key, self._initial_domain)

    def save(self, key, value):
        self.saved[key] = value


class FakeSharedState:
    def __init__(self, domain):
        self._config_section = DummyConfigSection(domain)
        self.values = {
            "config": lambda section: self._config_section,
            "user_agent": "QuasarrTestAgent/1.0",
            "internal_address": "http://127.0.0.1:9696",
        }

    def is_valid_release(self, title, request_from, search_string, season, episode):
        if not search_string:
            return True
        return search_string.lower() in title.lower()

    def normalize_magazine_title(self, title):
        return title

    def is_imdb_id(self, candidate):
        if isinstance(candidate, str) and candidate.lower().startswith("tt") and candidate[2:].isdigit():
            return candidate
        return None

    def convert_to_mb(self, item):
        return real_convert_to_mb(item)


class DummyResponse:
    def __init__(self, text, url):
        self.text = text
        self.url = url
        self.status_code = 200

    def raise_for_status(self):
        return None


class MaisonEnergySearchTests(unittest.TestCase):
    def setUp(self):
        self.shared_state = FakeSharedState("maison.energy")

    def test_postman_user_agent_defaults_to_films(self):
        self.assertEqual(me._get_category("PostmanRuntime/7.43.3"), "films")

    @patch("quasarr.search.sources.me.requests.get")
    def test_search_results_are_parsed_correctly(self, mock_get):
        final_url = "https://www.maison.energy/?p=films&search=chien"
        
        def side_effect(url, headers=None, timeout=None):
            if "search=" in url:
                return DummyResponse(MAISON_SEARCH_HTML, final_url)
            return DummyResponse(MAISON_FILM_DETAIL_HTML, url)

        mock_get.side_effect = side_effect

        releases = me.me_search(self.shared_state,
                                start_time=0,
                                request_from="Radarr",
                                search_string="chien")

        self.assertTrue(self.shared_state._config_section.saved)
        self.assertEqual(self.shared_state._config_section.saved.get("me"), "www.maison.energy")
        self.assertEqual(len(releases), 25)

        first_release = releases[0]
        self.assertEqual(first_release["type"], "protected")

        details = first_release["details"]
        self.assertEqual(details["title"], "gabe.un.amour.de.chien.DVDRIP.FRENCH")
        self.assertEqual(details["source"], "https://www.maison.energy/?p=film&id=186-gabe-un-amour-de-chien")
        self.assertEqual(details["mirror"], None)
        self.assertEqual(details["hostname"], "me")
        self.assertEqual(details["date"], "2017")
        self.assertEqual(details["category"], "2000")
        self.assertEqual(details["size"], 698 * 1024 * 1024)

        parsed_link = urlparse(details["link"])
        payload = parse_qs(parsed_link.query)["payload"][0]
        decoded = urlsafe_b64decode(payload).decode("utf-8")
        self.assertEqual(decoded,
                         "gabe.un.amour.de.chien.DVDRIP.FRENCH|https://www.maison.energy/?p=film&id=186-gabe-un-amour-de-chien|None|698|www.maison.energy|None")

        final_config = self.shared_state.values["config"]("Hostnames")
        self.assertEqual(final_config.get("me"), "www.maison.energy")
        self.assertGreater(mock_get.call_count, 1)


    @patch("quasarr.search.sources.me.requests.get")
    def test_series_category_is_used_for_sonarr(self, mock_get):
        final_url = "https://www.maison.energy/?p=series&search=chien"
        mock_get.return_value = DummyResponse(MAISON_SEARCH_HTML, final_url)

        releases = me.me_search(self.shared_state,
                                start_time=0,
                                request_from="Sonarr",
                                search_string="chien",
                                season=1,
                                episode=1)

        requested_url = mock_get.call_args_list[0][0][0]
        self.assertIn("?p=series&search=chien", requested_url)
        self.assertTrue(releases)
        self.assertEqual(self.shared_state._config_section.saved.get("me"), "www.maison.energy")


    @patch("quasarr.search.sources.me.requests.get")
    def test_manga_category_is_used_for_sonarr_anime(self, mock_get):
        final_url = "https://www.maison.energy/?p=mangas&search=chien"
        mock_get.return_value = DummyResponse(MAISON_SEARCH_HTML, final_url)

        releases = me.me_search(self.shared_state,
                                start_time=0,
                                request_from="Sonarr (Anime)",
                                search_string="chien",
                                season=1,
                                episode=1)

        requested_url = mock_get.call_args_list[0][0][0]
        self.assertIn("?p=mangas&search=chien", requested_url)
        self.assertTrue(releases)
        self.assertEqual(self.shared_state._config_section.saved.get("me"), "www.maison.energy")


    @patch("quasarr.search.sources.me.get_localized_title")
    @patch("quasarr.search.sources.me.requests.get")
    def test_imdb_id_is_preserved_in_payload(self, mock_get, mock_localized):
        mock_localized.return_value = "chien"
        final_url = "https://www.maison.energy/?p=films&search=chien"
        mock_get.return_value = DummyResponse(MAISON_SEARCH_HTML, final_url)

        releases = me.me_search(self.shared_state,
                                start_time=0,
                                request_from="Radarr",
                                search_string="tt0123456")

        self.assertTrue(releases)

        payload_url = releases[0]["details"]["link"]
        payload = parse_qs(urlparse(payload_url).query)["payload"][0]
        decoded = urlsafe_b64decode(payload).decode("utf-8").split("|")

        self.assertEqual(decoded[-1], "tt0123456")
        self.assertEqual(releases[0]["details"].get("imdb_id"), "tt0123456")


class MaisonEnergyDownloadTests(unittest.TestCase):
    def setUp(self):
        self.shared_state = FakeSharedState("www.maison.energy")

    @patch("quasarr.downloads.sources.me.requests.get")
    def test_download_links_exclude_streaming(self, mock_get):
        final_url = "https://www.maison.energy/?p=film&id=5558-chien"
        mock_get.return_value = DummyResponse(MAISON_FILM_DETAIL_HTML, final_url)

        data = download_me.get_me_download_links(self.shared_state,
                                                 url=final_url,
                                                 mirror=None,
                                                 title="Chien - HDRIP (FRENCH)")

        links = data.get("links")
        self.assertIsNotNone(links)
        self.assertEqual(len(links), 3)

        hosters = {hoster.lower() for _, hoster in links}
        self.assertIn("nitroflare", hosters)
        self.assertIn("rapidgator", hosters)
        self.assertIn("1fichier", hosters)
        self.assertNotIn("streamango", hosters)

        for href, hoster in links:
            self.assertTrue(href.startswith("https://"))
            self.assertNotIn("rl=a1", href)
            self.assertNotIn("regarder", href.lower())
            self.assertNotEqual(hoster.lower(), "dl-protect")

    @patch("quasarr.downloads.sources.me.requests.get")
    def test_download_links_include_additional_hosters(self, mock_get):
        final_url = "https://www.maison.energy/?p=film&id=52274-sebastian"
        mock_get.return_value = DummyResponse(MAISON_FILM_DETAIL_HTML_WITH_EXTRA_HOSTERS, final_url)

        data = download_me.get_me_download_links(self.shared_state,
                                                 url=final_url,
                                                 mirror=None,
                                                 title="Sebastian - WEB-DL 1080p (VOSTFR)")

        links = data.get("links")
        self.assertIsNotNone(links)
        self.assertEqual(len(links), 6)

        expected_hosters = {
            "dailyuploads",
            "uploady",
            "turbobit",
            "rapidgator",
            "nitroflare",
            "1fichier",
        }

        hosters = {hoster.lower() for _, hoster in links}
        self.assertEqual(hosters, expected_hosters)

        for href, _ in links:
            self.assertTrue(href.startswith("https://"))
            self.assertIn("rl=a2", href)

    @patch("quasarr.downloads.sources.me.requests.get")
    def test_manga_download_links_include_episode_labels(self, mock_get):
        final_url = "https://www.maison.energy/?p=manga&id=4042"
        mock_get.return_value = DummyResponse(MAISON_MANGA_DETAIL_HTML, final_url)

        data = download_me.get_me_download_links(self.shared_state,
                                                 url=final_url,
                                                 mirror=None,
                                                 title="Example Anime - WEB-DL")

        links = data.get("links")
        self.assertIsNotNone(links)
        self.assertEqual(len(links), 6)

        labels = [label for _, label in links]
        self.assertIn("DailyUploads - Episode 0", labels)
        self.assertTrue(all("Episode" in label for label in labels))

        daily = [label for label in labels if label.startswith("DailyUploads")]
        self.assertEqual(len(daily), 3)

        for href, label in links:
            self.assertNotIn("stream-0", href)
            self.assertTrue(href.startswith("https://"))


if __name__ == "__main__":
    unittest.main()
