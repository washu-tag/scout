<#import "template.ftl" as layout>
<@layout.registrationLayout; section>
    <#if section = "header">
        ${msg("termsTitle")}
    <#elseif section = "form">
    <div id="kc-terms-text" style="text-align: left; line-height: 1.6;">
        ${kcSanitize(msg("termsBody"))?no_esc}
    </div>
    <form class="form-actions" action="${url.loginAction}" method="POST" id="kc-terms-form">
        <input class="pf-c-button pf-m-primary pf-m-block btn-lg" name="accept" id="kc-accept" type="submit" value="${msg("doAccept")}"/>
        <div style="margin-top: 1em; text-align: center;">
            <button class="pf-c-button pf-m-link" name="cancel" id="kc-decline" type="submit" value="${msg("doDecline")}">${msg("doDecline")}</button>
        </div>
    </form>
    <div class="clearfix"></div>
    </#if>
</@layout.registrationLayout>
